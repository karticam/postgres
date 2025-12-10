/*-------------------------------------------------------------------------
 *
 * bloomfilter.c
 *		Space-efficient set membership testing
 *
 * A Bloom filter is a probabilistic data structure that is used to test an
 * element's membership of a set.  False positives are possible, but false
 * negatives are not; a test of membership of the set returns either "possibly
 * in set" or "definitely not in set".  This is typically very space efficient,
 * which can be a decisive advantage.
 *
 * Elements can be added to the set, but not removed.  The more elements that
 * are added, the larger the probability of false positives.  Caller must hint
 * an estimated total size of the set when the Bloom filter is initialized.
 * This is used to balance the use of memory against the final false positive
 * rate.
 *
 * The implementation is well suited to data synchronization problems between
 * unordered sets, especially where predictable performance is important and
 * some false positives are acceptable.  It's also well suited to cache
 * filtering problems where a relatively small and/or low cardinality set is
 * fingerprinted, especially when many subsequent membership tests end up
 * indicating that values of interest are not present.  That should save the
 * caller many authoritative lookups, such as expensive probes of a much larger
 * on-disk structure.
 *
 * Copyright (c) 2018-2025, PostgreSQL Global Development Group
 *
 * IDENTIFICATION
 *	  src/backend/lib/bloomfilter.c
 *
 *-------------------------------------------------------------------------
 */
#include "postgres.h"

#include <math.h>

#include "common/hashfn.h"
#include "lib/bloomfilter.h"
#include "port/atomics.h"
#include "port/pg_bitutils.h"

#define MAX_HASH_FUNCS		10

struct bloom_filter
{
	/* K hash functions are used, seeded by caller's seed */
	int			k_hash_funcs;
	uint64		seed;
	/* m is bitset size, in bits.  Must be a power of two <= 2^32.  */
	uint64		m;
	uint32		bitset_bytes;	/* logical size in bytes (m / 8) */
	uint32		bitset_words;	/* physical storage in uint32 words */
	bool		shared;			/* true if filter lives in shared memory */
	/* instrumentation counters */
	pg_atomic_uint64 insert_count;
	pg_atomic_uint64 scan_count; /* number of membership tests */
	pg_atomic_uint64 reject_count;	/* bloom_lacks_element returned true */
	pg_atomic_uint64 pass_count;	/* bloom_lacks_element returned false */
	uint32		bitset[FLEXIBLE_ARRAY_MEMBER];
};

static int	my_bloom_power(uint64 target_bitset_bits);
static int	optimal_k(uint64 bitset_bits, int64 total_elems);
static void k_hashes(bloom_filter *filter, uint32 *hashes, unsigned char *elem,
					 size_t len);
static inline uint32 mod_m(uint32 val, uint64 m);
static void bloom_compute_layout_from_size(uint64 size_bytes,
										   uint64 *bitset_bits,
										   uint32 *bitset_bytes,
										   uint32 *bitset_words);
static bloom_filter *bloom_init_common(void *space, Size space_size,
									   uint64 bitset_bits,
									   uint32 bitset_bytes,
									   uint32 bitset_words,
									   int k_hash_funcs,
									   uint64 seed,
									   bool shared);

/*
 * Create Bloom filter in caller's memory context.  We aim for a false positive
 * rate of between 1% and 2% when bitset size is not constrained by memory
 * availability.
 *
 * total_elems is an estimate of the final size of the set.  It should be
 * approximately correct, but the implementation can cope well with it being
 * off by perhaps a factor of five or more.  See "Bloom Filters in
 * Probabilistic Verification" (Dillinger & Manolios, 2004) for details of why
 * this is the case.
 *
 * bloom_work_mem is sized in KB, in line with the general work_mem convention.
 * This determines the size of the underlying bitset (trivial bookkeeping space
 * isn't counted).  The bitset is always sized as a power of two number of
 * bits, and the largest possible bitset is 512MB (2^32 bits).  The
 * implementation allocates only enough memory to target its standard false
 * positive rate, using a simple formula with caller's total_elems estimate as
 * an input.  The bitset might be as small as 1MB, even when bloom_work_mem is
 * much higher.
 *
 * The Bloom filter is seeded using a value provided by the caller.  Using a
 * distinct seed value on every call makes it unlikely that the same false
 * positives will reoccur when the same set is fingerprinted a second time.
 * Callers that don't care about this pass a constant as their seed, typically
 * 0.  Callers can also use a pseudo-random seed, eg from pg_prng_uint64().
 */
bloom_filter *
bloom_create(int64 total_elems, int bloom_work_mem, uint64 seed)
{
	int			bloom_power;
	uint64		bitset_bytes;
	uint64		bitset_bits;
	uint32		bitset_words;
	Size		memsize;
	bloom_filter *filter;

	/*
	 * Aim for two bytes per element; this is sufficient to get a false
	 * positive rate below 1%, independent of the size of the bitset or total
	 * number of elements.  Also, if rounding down the size of the bitset to
	 * the next lowest power of two turns out to be a significant drop, the
	 * false positive rate still won't exceed 2% in almost all cases.
	 */
	bitset_bytes = Min(bloom_work_mem * UINT64CONST(1024), total_elems * 2);
	bitset_bytes = Max(UINT64CONST(1024) * 1024, bitset_bytes);

	/*
	 * Size in bits should be the highest power of two <= target.  bitset_bits
	 * is uint64 because PG_UINT32_MAX is 2^32 - 1, not 2^32.
	 */
	bloom_power = my_bloom_power(bitset_bytes * BITS_PER_BYTE);
	bitset_bits = UINT64CONST(1) << bloom_power;
	bitset_bytes = bitset_bits / BITS_PER_BYTE;
	bitset_words = (uint32) ((bitset_bytes + sizeof(uint32) - 1) /
							 sizeof(uint32));
	memsize = offsetof(bloom_filter, bitset) +
		sizeof(uint32) * bitset_words;
	filter = palloc(memsize);

	return bloom_init_common(filter, memsize,
							 bitset_bits,
							 (uint32) bitset_bytes,
							 bitset_words,
							 optimal_k(bitset_bits, total_elems),
							 seed,
							 false);
}

/*
 * Create Bloom filter with explicit sizing parameters.
 *
 * k_hash_funcs: number of hash functions to use (clamped to [1, MAX_HASH_FUNCS])
 * size_bytes: size of the bitset in bytes (will be rounded down to power-of-two)
 */
bloom_filter *
bloom_create_with_params(uint64 size_bytes, int k_hash_funcs, uint64 seed)
{
	uint64		bitset_bits;
	uint32		bitset_bytes;
	uint32		bitset_words;
	Size		memsize;
	bloom_filter *filter;

	bloom_compute_layout_from_size(size_bytes, &bitset_bits,
								   &bitset_bytes, &bitset_words);

	memsize = offsetof(bloom_filter, bitset) +
		sizeof(uint32) * bitset_words;
	filter = palloc(memsize);

	return bloom_init_common(filter, memsize,
							 bitset_bits,
							 bitset_bytes,
							 bitset_words,
							 k_hash_funcs,
							 seed,
							 false);
}

Size
bloom_get_memory_size(uint64 size_bytes)
{
	uint64		bitset_bits;
	uint32		bitset_bytes;
	uint32		bitset_words;

	bloom_compute_layout_from_size(size_bytes, &bitset_bits,
								   &bitset_bytes, &bitset_words);

	return offsetof(bloom_filter, bitset) +
		sizeof(uint32) * bitset_words;
}

bloom_filter *
bloom_create_in_place(void *space, Size space_size, uint64 size_bytes,
					  int k_hash_funcs, uint64 seed, bool shared)
{
	uint64		bitset_bits;
	uint32		bitset_bytes;
	uint32		bitset_words;
	Size		memsize;

	bloom_compute_layout_from_size(size_bytes, &bitset_bits,
								   &bitset_bytes, &bitset_words);
	memsize = offsetof(bloom_filter, bitset) +
		sizeof(uint32) * bitset_words;

	if (space_size < memsize)
		elog(ERROR, "insufficient space for bloom filter");

	return bloom_init_common(space, memsize,
							 bitset_bits,
							 bitset_bytes,
							 bitset_words,
							 k_hash_funcs,
							 seed,
							 shared);
}

void
bloom_reset(bloom_filter *filter)
{
	Size		storage_bytes = (Size) filter->bitset_words * sizeof(uint32);

	memset(filter->bitset, 0, storage_bytes);
	pg_atomic_write_u64(&filter->insert_count, 0);
	pg_atomic_write_u64(&filter->scan_count, 0);
	pg_atomic_write_u64(&filter->reject_count, 0);
	pg_atomic_write_u64(&filter->pass_count, 0);
}

static bloom_filter *
bloom_init_common(void *space, Size space_size,
				  uint64 bitset_bits,
				  uint32 bitset_bytes,
				  uint32 bitset_words,
				  int k_hash_funcs,
				  uint64 seed,
				  bool shared)
{
	Size		storage_bytes = sizeof(uint32) * bitset_words;
	Size		required = offsetof(bloom_filter, bitset) + storage_bytes;
	bloom_filter *filter = (bloom_filter *) space;
	int			k;

	if (space_size < required)
		elog(ERROR, "insufficient space for bloom filter");

	MemSet(filter, 0, required);

	k = Max(1, Min(k_hash_funcs, MAX_HASH_FUNCS));
	filter->k_hash_funcs = k;
	filter->seed = seed;
	filter->m = bitset_bits;
	filter->bitset_bytes = bitset_bytes;
	filter->bitset_words = bitset_words;
	filter->shared = shared;
	pg_atomic_init_u64(&filter->insert_count, 0);
	pg_atomic_init_u64(&filter->scan_count, 0);
	pg_atomic_init_u64(&filter->reject_count, 0);
	pg_atomic_init_u64(&filter->pass_count, 0);

	return filter;
}

static void
bloom_compute_layout_from_size(uint64 size_bytes,
							   uint64 *bitset_bits,
							   uint32 *bitset_bytes,
							   uint32 *bitset_words)
{
	/* enforce at least one byte and clamp to power-of-two bits <= 2^32 */
	if (size_bytes < 1)
		size_bytes = 1;

	if (size_bytes > (UINT64CONST(1) << 29))
		size_bytes = (UINT64CONST(1) << 29);

	*bitset_bits = UINT64CONST(1) << my_bloom_power(size_bytes * BITS_PER_BYTE);
	*bitset_bytes = (uint32) (*bitset_bits / BITS_PER_BYTE);
	*bitset_words = (uint32) ((*bitset_bytes + sizeof(uint32) - 1) /
							  sizeof(uint32));
	if (*bitset_words == 0)
		*bitset_words = 1;
}

/*
 * Free Bloom filter
 */
void
bloom_free(bloom_filter *filter)
{
	if (filter->shared)
		return;
	pfree(filter);
}

/*
 * Add element to Bloom filter
 */
void
bloom_add_element(bloom_filter *filter, unsigned char *elem, size_t len)
{
	uint32		hashes[MAX_HASH_FUNCS];
	int			i;
	uint32	   *words = filter->bitset;
	pg_atomic_uint32 *atomic_words = (pg_atomic_uint32 *) filter->bitset;

	k_hashes(filter, hashes, elem, len);

	for (i = 0; i < filter->k_hash_funcs; i++)
	{
		uint32		bitno = hashes[i];
		uint32		word_index = bitno >> 5;
		uint32		mask = 1U << (bitno & 31);

		if (filter->shared)
			pg_atomic_fetch_or_u32(&atomic_words[word_index], mask);
		else
			words[word_index] |= mask;
	}

	/* pg_atomic_fetch_add_u64(&filter->insert_count, 1); */
}

/*
 * Test if Bloom filter definitely lacks element.
 *
 * Returns true if the element is definitely not in the set of elements
 * observed by bloom_add_element().  Otherwise, returns false, indicating that
 * element is probably present in set.
 */
bool
bloom_lacks_element(bloom_filter *filter, unsigned char *elem, size_t len)
{
	uint32		hashes[MAX_HASH_FUNCS];
	int			i;
	uint32	   *words = filter->bitset;
	pg_atomic_uint32 *atomic_words = (pg_atomic_uint32 *) filter->bitset;
	bool		reject = false;

	k_hashes(filter, hashes, elem, len);

	/* pg_atomic_fetch_add_u64(&filter->scan_count, 1); */

	for (i = 0; i < filter->k_hash_funcs; i++)
	{
		uint32		bitno = hashes[i];
		uint32		word_index = bitno >> 5;
		uint32		mask = 1U << (bitno & 31);
		uint32		word;

		if (filter->shared)
			word = pg_atomic_read_u32(&atomic_words[word_index]);
		else
			word = words[word_index];

		if ((word & mask) == 0)
		{
			reject = true;
			break;
		}
	}

	if (reject)
	{
		/* pg_atomic_fetch_add_u64(&filter->reject_count, 1); */
		return true;
	}

	/* pg_atomic_fetch_add_u64(&filter->pass_count, 1); */
	return false;
}

/*
 * What proportion of bits are currently set?
 *
 * Returns proportion, expressed as a multiplier of filter size.  That should
 * generally be close to 0.5, even when we have more than enough memory to
 * ensure a false positive rate within target 1% to 2% band, since more hash
 * functions are used as more memory is available per element.
 *
 * This is the only instrumentation that is low overhead enough to appear in
 * debug traces.  When debugging Bloom filter code, it's likely to be far more
 * interesting to directly test the false positive rate.
 */
double
bloom_prop_bits_set(bloom_filter *filter)
{
	int			bitset_bytes = filter->bitset_bytes;
	uint64		bits_set = pg_popcount((char *) filter->bitset, bitset_bytes);

	return bits_set / (double) filter->m;
}

/* Instrumentation accessors */
uint64
bloom_get_insert_count(bloom_filter *filter)
{
	if (filter == NULL)
		return 0;
	return pg_atomic_read_u64(&filter->insert_count);
}

uint64
bloom_get_scan_count(bloom_filter *filter)
{
	if (filter == NULL)
		return 0;
	return pg_atomic_read_u64(&filter->scan_count);
}

uint64
bloom_get_reject_count(bloom_filter *filter)
{
	if (filter == NULL)
		return 0;
	return pg_atomic_read_u64(&filter->reject_count);
}

uint64
bloom_get_pass_count(bloom_filter *filter)
{
	if (filter == NULL)
		return 0;
	return pg_atomic_read_u64(&filter->pass_count);
}

/*
 * Which element in the sequence of powers of two is less than or equal to
 * target_bitset_bits?
 *
 * Value returned here must be generally safe as the basis for actual bitset
 * size.
 *
 * Bitset is never allowed to exceed 2 ^ 32 bits (512MB).  This is sufficient
 * for the needs of all current callers, and allows us to use 32-bit hash
 * functions.  It also makes it easy to stay under the MaxAllocSize restriction
 * (caller needs to leave room for non-bitset fields that appear before
 * flexible array member, so a 1GB bitset would use an allocation that just
 * exceeds MaxAllocSize).
 */
static int
my_bloom_power(uint64 target_bitset_bits)
{
	int			bloom_power = -1;

	while (target_bitset_bits > 0 && bloom_power < 32)
	{
		bloom_power++;
		target_bitset_bits >>= 1;
	}

	return bloom_power;
}

/*
 * Determine optimal number of hash functions based on size of filter in bits,
 * and projected total number of elements.  The optimal number is the number
 * that minimizes the false positive rate.
 */
static int
optimal_k(uint64 bitset_bits, int64 total_elems)
{
	int			k = rint(log(2.0) * bitset_bits / total_elems);

	return Max(1, Min(k, MAX_HASH_FUNCS));
}

/*
 * Generate k hash values for element.
 *
 * Caller passes array, which is filled-in with k values determined by hashing
 * caller's element.
 *
 * Only 2 real independent hash functions are actually used to support an
 * interface of up to MAX_HASH_FUNCS hash functions; enhanced double hashing is
 * used to make this work.  The main reason we prefer enhanced double hashing
 * to classic double hashing is that the latter has an issue with collisions
 * when using power of two sized bitsets.  See Dillinger & Manolios for full
 * details.
 */
static void
k_hashes(bloom_filter *filter, uint32 *hashes, unsigned char *elem, size_t len)
{
	uint64		hash;
	uint32		x,
				y;
	uint64		m;
	int			i;

	/* Use 64-bit hashing to get two independent 32-bit hashes */
	hash = DatumGetUInt64(hash_any_extended(elem, len, filter->seed));
	x = (uint32) hash;
	y = (uint32) (hash >> 32);
	m = filter->m;

	x = mod_m(x, m);
	y = mod_m(y, m);

	/* Accumulate hashes */
	hashes[0] = x;
	for (i = 1; i < filter->k_hash_funcs; i++)
	{
		x = mod_m(x + y, m);
		y = mod_m(y + i, m);

		hashes[i] = x;
	}
}

/*
 * Calculate "val MOD m" inexpensively.
 *
 * Assumes that m (which is bitset size) is a power of two.
 *
 * Using a power of two number of bits for bitset size allows us to use bitwise
 * AND operations to calculate the modulo of a hash value.  It's also a simple
 * way of avoiding the modulo bias effect.
 */
static inline uint32
mod_m(uint32 val, uint64 m)
{
	Assert(m <= PG_UINT32_MAX + UINT64CONST(1));
	Assert(((m - 1) & m) == 0);

	return val & (m - 1);
}

/*
 * Merge two Bloom filters by ORing the bitsets.
 *
 * The source filter must be non-shared (local).
 * The target filter can be shared or non-shared.
 * Both filters must have the same size and parameters (this is verified by assertion).
 */
/*
 * Merge two Bloom filters by ORing the bitsets.
 *
 * The source filter must be non-shared (local).
 * The target filter can be shared or non-shared.
 * Both filters must have the same size and parameters (this is verified by assertion).
 */
void
bloom_or(bloom_filter *target, bloom_filter *source)
{
	uint64		*src_words = (uint64 *) source->bitset;
	pg_atomic_uint64 *target_atomic_words = (pg_atomic_uint64 *) target->bitset;
	uint64		*target_words = (uint64 *) target->bitset;
	size_t		num_u64_words;
	size_t		i;

	Assert(target->bitset_words == source->bitset_words);
	Assert(target->k_hash_funcs == source->k_hash_funcs);
	Assert(target->seed == source->seed);
	Assert(!source->shared);

	/*
	 * Process in 64-bit chunks for efficiency.
	 * bitset_bytes is guaranteed to be a power of 2 and aligned, so this is safe.
	 */
	num_u64_words = target->bitset_bytes / sizeof(uint64);

	for (i = 0; i < num_u64_words; i++)
	{
		uint64		word = src_words[i];

		if (word != 0)
		{
			if (target->shared)
				pg_atomic_fetch_or_u64(&target_atomic_words[i], word);
			else
				target_words[i] |= word;
		}
	}
}

/*
 * Merge two Bloom filters by ORing the bitsets without using atomics.
 *
 * This function assumes that the caller holds an exclusive lock on the
 * target filter if it is shared, preventing concurrent access.
 */
void
bloom_or_nonatomic(bloom_filter *target, bloom_filter *source)
{
	uint64		*src_words = (uint64 *) source->bitset;
	uint64		*target_words = (uint64 *) target->bitset;
	size_t		num_u64_words;
	size_t		i;

	Assert(target->bitset_words == source->bitset_words);
	Assert(target->k_hash_funcs == source->k_hash_funcs);
	Assert(target->seed == source->seed);
	Assert(!source->shared);

	/*
	 * Process in 64-bit chunks for efficiency.
	 */
	num_u64_words = target->bitset_bytes / sizeof(uint64);

	for (i = 0; i < num_u64_words; i++)
	{
		uint64		word = src_words[i];

		if (word != 0)
			target_words[i] |= word;
	}
}

/*
 * Get the properties of a Bloom filter.
 *
 * This allows the caller to create a new Bloom filter with the same properties.
 */
void
bloom_get_properties(bloom_filter *filter, uint64 *size_bytes, int *k_hash_funcs, uint64 *seed)
{
	*size_bytes = filter->bitset_bytes;
	*k_hash_funcs = filter->k_hash_funcs;
	*seed = filter->seed;
}
