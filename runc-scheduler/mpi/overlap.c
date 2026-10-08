#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

static volatile double sink;
static void compute(long loops, MPI_Request *request) {
    double x = 0.1234567;
    for (long i = 0; i < loops; i++) {
        x = sin(x) + 0.00001 * (double)(i % 13);
        if (request && i % 16384 == 0) { int done; MPI_Test(request, &done, MPI_STATUS_IGNORE); }
    }
    sink = x;
}

static double trial(int overlapping, double *input, double *output, int count, long loops) {
    MPI_Barrier(MPI_COMM_WORLD);
    double start = MPI_Wtime();
    if (overlapping) {
        MPI_Request request;
        MPI_Iallreduce(input, output, count, MPI_DOUBLE, MPI_SUM, MPI_COMM_WORLD, &request);
        compute(loops, &request);
        MPI_Wait(&request, MPI_STATUS_IGNORE);
    } else {
        compute(loops, NULL);
        MPI_Allreduce(input, output, count, MPI_DOUBLE, MPI_SUM, MPI_COMM_WORLD);
    }
    double elapsed = MPI_Wtime() - start, maximum;
    MPI_Allreduce(&elapsed, &maximum, 1, MPI_DOUBLE, MPI_MAX, MPI_COMM_WORLD);
    return maximum;
}

int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank, size;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank); MPI_Comm_size(MPI_COMM_WORLD, &size);
    char host[MPI_MAX_PROCESSOR_NAME]; int hostlen;
    MPI_Get_processor_name(host, &hostlen);
    printf("rank=%d host=%s\n", rank, host); fflush(stdout);
    int count = argc > 1 ? atoi(argv[1]) : 1048576;
    long loops = argc > 2 ? atol(argv[2]) : 2000000;
    int rounds = argc > 3 ? atoi(argv[3]) : 5;
    if (count < 1 || count > 16777216 || loops < 1 || rounds < 1 || rounds > 100) MPI_Abort(MPI_COMM_WORLD, 2);
    double *input = malloc((size_t)count * sizeof(double));
    double *output = malloc((size_t)count * sizeof(double));
    if (!input || !output) MPI_Abort(MPI_COMM_WORLD, 2);
    for (int i = 0; i < count; i++) input[i] = rank + 1;
    double serial = 0.0, overlap = 0.0;
    (void)trial(0, input, output, count, loops);
    (void)trial(1, input, output, count, loops);
    int ok = 1;
    for (int r = 0; r < rounds; r++) {
        for (int step = 0; step < 2; step++) {
            int overlapping = (step + r) % 2;
            double result = trial(overlapping, input, output, count, loops);
            if (overlapping) overlap += result; else serial += result;
            for (int i = 0; i < count; i++) if (output[i] != size * (size + 1) / 2.0) ok = 0;
        }
    }
    int all_ok;
    MPI_Allreduce(&ok, &all_ok, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD);
    if (rank == 0) {
        printf("RCS_RESULT {\"test\":\"overlap\",\"ranks\":%d,\"elements\":%d,\"computeLoops\":%ld,\"rounds\":%d,\"serialSeconds\":%.9f,\"overlapSeconds\":%.9f,\"speedup\":%.6f,\"passed\":%s}\n",
               size, count, loops, rounds, serial / rounds, overlap / rounds,
               overlap > 0 ? serial / overlap : 0.0, all_ok ? "true" : "false");
        fflush(stdout);
    }
    free(input); free(output);
    MPI_Finalize();
    return all_ok ? 0 : 1;
}
