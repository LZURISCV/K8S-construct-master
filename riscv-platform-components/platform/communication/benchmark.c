#include "rv_collective.h"
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void *buffer(rv_context *c,size_t n) {
    void *p=calloc(n?n:1,sizeof(long double));if(!p) MPI_Abort(c->world,99);return p;
}
static int integer(const char *v) {
    char *end;long n=strtol(v,&end,10);
    if(!*v || *end || n<0 || n>INT_MAX) {fprintf(stderr,"invalid integer\n");exit(2);}
    return (int)n;
}
int main(int argc,char **argv) {
    MPI_Init(&argc,&argv);
    rv_context c;rv_init(&c,MPI_COMM_WORLD);
    if(argc<3) {
        if(c.rank==0) fprintf(stderr,"rv-bench bcast|reduce|allgather|fusion native|hierarchical|serial|overlap [count repeats chunk work root]\n");
        MPI_Abort(c.world,2);
    }
    const char *op=argv[1],*mode=argv[2];
    int n=argc>3?integer(argv[3]):1024,repeats=argc>4?integer(argv[4]):7;
    int chunk=argc>5?integer(argv[5]):256,work=argc>6?integer(argv[6]):32,root=argc>7?integer(argv[7]):0;
    if(!n || !repeats || !chunk || root>=c.size || n>INT_MAX/c.size) MPI_Abort(c.world,2);
    if(strcmp(op,"bcast") && strcmp(op,"reduce") && strcmp(op,"allgather") && strcmp(op,"fusion")) MPI_Abort(c.world,2);
    if(!strcmp(op,"fusion")) {
        if(strcmp(mode,"serial") && strcmp(mode,"overlap")) MPI_Abort(c.world,2);
    } else if(strcmp(mode,"native") && strcmp(mode,"hierarchical")) MPI_Abort(c.world,2);
    int bits=LDBL_MANT_DIG,minbits,maxbits;
    char processor[MPI_MAX_PROCESSOR_NAME]={0};int processor_length;
    MPI_Get_processor_name(processor,&processor_length);
    char *processors=calloc((size_t)c.size,MPI_MAX_PROCESSOR_NAME);
    if(!processors)MPI_Abort(c.world,99);
    MPI_Allgather(processor,MPI_MAX_PROCESSOR_NAME,MPI_CHAR,processors,MPI_MAX_PROCESSOR_NAME,MPI_CHAR,c.world);
    char worker[256]={0};const char *worker_env=getenv("RV_WORKER_POD");
    if(worker_env) {
        size_t length=strlen(worker_env);
        if(length>=sizeof(worker))MPI_Abort(c.world,2);
        for(size_t i=0;i<length;i++)if(!((worker_env[i]>='a'&&worker_env[i]<='z') ||
            (worker_env[i]>='0'&&worker_env[i]<='9') || worker_env[i]=='-' || worker_env[i]=='.'))MPI_Abort(c.world,2);
        memcpy(worker,worker_env,length);
    }
    char *workers=calloc((size_t)c.size,sizeof(worker));if(!workers)MPI_Abort(c.world,99);
    MPI_Allgather(worker,sizeof(worker),MPI_CHAR,workers,sizeof(worker),MPI_CHAR,c.world);
    MPI_Allreduce(&bits,&minbits,1,MPI_INT,MPI_MIN,c.world);MPI_Allreduce(&bits,&maxbits,1,MPI_INT,MPI_MAX,c.world);
    if(minbits!=maxbits) {if(!c.rank) fprintf(stderr,"heterogeneous long double precision differs\n");MPI_Abort(c.world,3);}
    long double *in=buffer(&c,n),*out=buffer(&c,(size_t)n*c.size),*reference=buffer(&c,(size_t)n*c.size),*computed=buffer(&c,n);
    for(int i=0;i<n;i++) in[i]=!strcmp(op,"bcast")?(c.rank==root?42.0L+(long double)i/17:0):(long double)(c.rank+1)+(long double)i/17;
    long double local_error=0;
    if(!strcmp(op,"fusion")) {rv_compute(in,computed,n,work);MPI_Allreduce(computed,reference,n,MPI_LONG_DOUBLE,MPI_SUM,c.world);}
    else if(!strcmp(op,"bcast")) {memcpy(reference,in,(size_t)n*sizeof(long double));MPI_Bcast(reference,n,MPI_LONG_DOUBLE,root,c.world);}
    else if(!strcmp(op,"reduce")) MPI_Reduce(in,reference,n,MPI_LONG_DOUBLE,MPI_SUM,root,c.world);
    else MPI_Allgather(in,n,MPI_LONG_DOUBLE,reference,n,MPI_LONG_DOUBLE,c.world);
    double total=0,best=1e300;int failures=0;
    for(int trial=-1;trial<repeats;trial++) {
        if(!strcmp(op,"bcast") && c.rank!=root) memset(in,0,(size_t)n*sizeof(long double));
        MPI_Barrier(c.world);double start=MPI_Wtime();
        if(!strcmp(op,"fusion")) {
            if(!strcmp(mode,"overlap")) rv_fused_sum(&c,in,out,n,chunk,work);
            else {rv_compute(in,computed,n,work);MPI_Allreduce(computed,out,n,MPI_LONG_DOUBLE,MPI_SUM,c.world);}
        } else if(!strcmp(op,"bcast")) {
            if(!strcmp(mode,"hierarchical")) rv_bcast(&c,in,n,root);else MPI_Bcast(in,n,MPI_LONG_DOUBLE,root,c.world);
            memcpy(out,in,(size_t)n*sizeof(long double));
        } else if(!strcmp(op,"reduce")) {
            if(!strcmp(mode,"hierarchical")) rv_reduce_sum(&c,in,out,n,root);else MPI_Reduce(in,out,n,MPI_LONG_DOUBLE,MPI_SUM,root,c.world);
        } else {
            if(!strcmp(mode,"hierarchical")) rv_allgather(&c,in,n,out);else MPI_Allgather(in,n,MPI_LONG_DOUBLE,out,n,MPI_LONG_DOUBLE,c.world);
        }
        double elapsed=MPI_Wtime()-start,worst;MPI_Allreduce(&elapsed,&worst,1,MPI_DOUBLE,MPI_MAX,c.world);
        if(trial>=0) {total+=worst;if(worst<best) best=worst;}
        int elements=!strcmp(op,"allgather")?n*c.size:n;
        if(strcmp(op,"reduce") || c.rank==root) for(int i=0;i<elements;i++) {
            long double error=fabsl(out[i]-reference[i])/fmaxl(1.0L,fabsl(reference[i]));
            if(!isfinite(out[i])) failures=1;
            if(error>local_error) local_error=error;
        }
    }
    long double error;int failed;MPI_Allreduce(&local_error,&error,1,MPI_LONG_DOUBLE,MPI_MAX,c.world);
    MPI_Allreduce(&failures,&failed,1,MPI_INT,MPI_MAX,c.world);
    long double tolerance=64.0L*c.size*LDBL_EPSILON;int leaders=0;
    for(int i=0;i<c.size;i++) if(c.leader_for_rank[i]==i) leaders++;
    if(!c.rank) {
      printf("{\"operation\":\"%s\",\"mode\":\"%s\",\"ranks\":%d,\"nodes\":%d,\"count\":%d,\"repeats\":%d,\"bytes_per_rank\":%zu,\"mantissa_bits\":%d,\"mean_seconds\":%.9g,\"best_seconds\":%.9g,\"relative_error\":%.9Lg,\"tolerance\":%.9Lg,\"correct\":%s,\"processor_names\":[",
      op,mode,c.size,leaders,n,repeats,(size_t)n*sizeof(long double),bits,total/repeats,best,error,tolerance,(error<=tolerance&&!failed)?"true":"false");
      for(int i=0;i<c.size;i++)printf("%s\"%s\"",i?",":"",processors+(size_t)i*MPI_MAX_PROCESSOR_NAME);
      printf("],\"worker_pod_names\":[");
      for(int i=0;i<c.size;i++)printf("%s\"%s\"",i?",":"",workers+(size_t)i*sizeof(worker));
      puts("]}");
    }
    free(workers);free(processors);free(in);free(out);free(reference);free(computed);rv_free(&c);MPI_Finalize();
    return error<=tolerance&&!failed?0:1;
}

