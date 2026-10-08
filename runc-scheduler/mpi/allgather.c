#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank, size, ok = 1, all_ok;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank); MPI_Comm_size(MPI_COMM_WORLD, &size);
    int local = rank + 1;
    int *values = calloc((size_t)size, sizeof(int));
    if (!values) MPI_Abort(MPI_COMM_WORLD, 2);
    MPI_Allgather(&local, 1, MPI_INT, values, 1, MPI_INT, MPI_COMM_WORLD);
    for (int i = 0; i < size; i++) if (values[i] != i + 1) ok = 0;
    MPI_Allreduce(&ok, &all_ok, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD);
    char host[MPI_MAX_PROCESSOR_NAME]; int hostlen;
    MPI_Get_processor_name(host, &hostlen);
    printf("rank=%d host=%s allgather=", rank, host);
    for (int i = 0; i < size; i++) printf("%s%d", i ? "," : "", values[i]);
    puts(""); fflush(stdout);
    if (rank == 0) printf("RCS_RESULT {\"test\":\"allgather\",\"ranks\":%d,\"allgatherVerified\":%s,\"passed\":%s}\n", size, all_ok ? "true" : "false", all_ok ? "true" : "false");
    free(values); MPI_Finalize(); return all_ok ? 0 : 1;
}
