/*-------------------------------------------------------------------------
 *
 * bloomfilter.h
 *	  Space-efficient set membership testing
 *
 * Copyright (c) 2018-2025, PostgreSQL Global Development Group
 *
 * IDENTIFICATION
 *    src/include/lib/bloomfilter.h
 *
 *-------------------------------------------------------------------------
 */
#ifndef BLOOMFILTER_H
#define BLOOMFILTER_H

#include "c.h"

typedef struct bloom_filter bloom_filter;

extern bloom_filter *bloom_create(int64 total_elems, int bloom_work_mem,
								  uint64 seed);
extern bloom_filter *bloom_create_with_params(uint64 size_bytes,
											  int k_hash_funcs,
											  uint64 seed);
extern bloom_filter *bloom_create_in_place(void *space, Size space_size,
										   uint64 size_bytes,
										   int k_hash_funcs,
										   uint64 seed, bool shared);
extern Size bloom_get_memory_size(uint64 size_bytes);
extern void bloom_free(bloom_filter *filter);
extern void bloom_reset(bloom_filter *filter);
extern void bloom_add_element(bloom_filter *filter, unsigned char *elem,
							  size_t len);
extern bool bloom_lacks_element(bloom_filter *filter, unsigned char *elem,
								size_t len);
extern double bloom_prop_bits_set(bloom_filter *filter);
extern void bloom_or(bloom_filter *target, bloom_filter *source);
extern void bloom_or_nonatomic(bloom_filter *target, bloom_filter *source);
extern void bloom_get_properties(bloom_filter *filter, uint64 *size_bytes, int *k_hash_funcs, uint64 *seed);

/* instrumentation accessors */
extern uint64 bloom_get_insert_count(bloom_filter *filter);
extern uint64 bloom_get_scan_count(bloom_filter *filter);
extern uint64 bloom_get_reject_count(bloom_filter *filter);
extern uint64 bloom_get_pass_count(bloom_filter *filter);

#endif							/* BLOOMFILTER_H */
