#include <mpi.h>
#include <stdio.h>
int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank, size, value, ok, all_ok;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank); MPI_Comm_size(MPI_COMM_WORLD, &size);
    value = rank == 0 ? 42 : 0;
    MPI_Bcast(&value, 1, MPI_INT, 0, MPI_COMM_WORLD);
    ok = value == 42;
    MPI_Allreduce(&ok, &all_ok, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD);
    char host[MPI_MAX_PROCESSOR_NAME]; int hostlen;
    MPI_Get_processor_name(host, &hostlen);
    printf("rank=%d host=%s broadcast=%d\n", rank, host, value); fflush(stdout);
    if (rank == 0) printf("RCS_RESULT {\"test\":\"broadcast\",\"ranks\":%d,\"value\":%d,\"passed\":%s}\n", size, value, all_ok ? "true" : "false");
    MPI_Finalize(); return all_ok ? 0 : 1;
}
