#include <mpi.h>
#include <stdio.h>
int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank, size, sum = 0, ok = 1;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank); MPI_Comm_size(MPI_COMM_WORLD, &size);
    int local = rank + 1;
    MPI_Reduce(&local, &sum, 1, MPI_INT, MPI_SUM, 0, MPI_COMM_WORLD);
    if (rank == 0) ok = sum == size * (size + 1) / 2;
    MPI_Bcast(&ok, 1, MPI_INT, 0, MPI_COMM_WORLD);
    char host[MPI_MAX_PROCESSOR_NAME]; int hostlen;
    MPI_Get_processor_name(host, &hostlen);
    printf("rank=%d host=%s reduceInput=%d\n", rank, host, local); fflush(stdout);
    if (rank == 0) printf("RCS_RESULT {\"test\":\"reduce\",\"ranks\":%d,\"sum\":%d,\"expected\":%d,\"passed\":%s}\n", size, sum, size * (size + 1) / 2, ok ? "true" : "false");
    MPI_Finalize(); return ok ? 0 : 1;
}
