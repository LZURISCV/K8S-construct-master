#ifndef RV_COLLECTIVE_H
#define RV_COLLECTIVE_H
#include <mpi.h>
typedef struct {
    MPI_Comm world, local, leaders;
    int rank, size, local_rank, local_size, leader_world, leader_rank;
    int *leader_for_rank;
} rv_context;
int rv_init(rv_context *, MPI_Comm);
void rv_free(rv_context *);
int rv_bcast(rv_context *, long double *, int, int);
int rv_reduce_sum(rv_context *, const long double *, long double *, int, int);
int rv_allgather(rv_context *, const long double *, int, long double *);
int rv_fused_sum(rv_context *, const long double *, long double *, int, int, int);
void rv_compute(const long double *, long double *, int, int);
#endif

