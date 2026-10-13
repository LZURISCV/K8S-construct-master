#include "rv_collective.h"
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { int e=(x); if(e!=MPI_SUCCESS) MPI_Abort(c->world,e); } while(0)
static void *alloc(rv_context *c, size_t n) {
    void *p=calloc(n?n:1,1);
    if(!p) MPI_Abort(c->world,99);
    return p;
}
static int valid(rv_context *c,int count,int root) {
    return count>=0 && root>=0 && root<c->size;
}
int rv_init(rv_context *c, MPI_Comm world) {
    memset(c,0,sizeof(*c)); c->leaders=MPI_COMM_NULL; c->world=world;
    CHECK(MPI_Comm_rank(world,&c->rank)); CHECK(MPI_Comm_size(world,&c->size));
    CHECK(MPI_Comm_split_type(world,MPI_COMM_TYPE_SHARED,c->rank,MPI_INFO_NULL,&c->local));
    CHECK(MPI_Comm_rank(c->local,&c->local_rank)); CHECK(MPI_Comm_size(c->local,&c->local_size));
    c->leader_world=c->rank;
    CHECK(MPI_Bcast(&c->leader_world,1,MPI_INT,0,c->local));
    CHECK(MPI_Comm_split(world,c->local_rank==0?0:MPI_UNDEFINED,c->rank,&c->leaders));
    c->leader_for_rank=alloc(c,(size_t)c->size*sizeof(int));
    CHECK(MPI_Allgather(&c->leader_world,1,MPI_INT,c->leader_for_rank,1,MPI_INT,world));
    if(c->local_rank==0) CHECK(MPI_Comm_rank(c->leaders,&c->leader_rank));
    return MPI_SUCCESS;
}
void rv_free(rv_context *c) {
    free(c->leader_for_rank);
    if(c->leaders!=MPI_COMM_NULL) MPI_Comm_free(&c->leaders);
    MPI_Comm_free(&c->local);
}
static int root_leader(rv_context *c,int root) {
    int leader=c->leader_for_rank[root],answer=0;
    for(int i=0;i<leader;i++) if(c->leader_for_rank[i]==i) answer++;
    return answer;
}
int rv_bcast(rv_context *c,long double *data,int count,int root) {
    if(!valid(c,count,root)) return MPI_ERR_ARG;
    int leader=c->leader_for_rank[root];
    if(c->leader_world==leader) {
        int root_local=c->rank==root?c->local_rank:-1,found;
        CHECK(MPI_Allreduce(&root_local,&found,1,MPI_INT,MPI_MAX,c->local));
        CHECK(MPI_Bcast(data,count,MPI_LONG_DOUBLE,found,c->local));
    }
    if(c->local_rank==0) CHECK(MPI_Bcast(data,count,MPI_LONG_DOUBLE,root_leader(c,root),c->leaders));
    CHECK(MPI_Bcast(data,count,MPI_LONG_DOUBLE,0,c->local));
    return MPI_SUCCESS;
}
int rv_reduce_sum(rv_context *c,const long double *in,long double *out,int count,int root) {
    if(!valid(c,count,root)) return MPI_ERR_ARG;
    long double *tmp=alloc(c,(size_t)count*sizeof(long double));
    long double *sum=alloc(c,(size_t)count*sizeof(long double));
    CHECK(MPI_Reduce(in,tmp,count,MPI_LONG_DOUBLE,MPI_SUM,0,c->local));
    if(c->local_rank==0) CHECK(MPI_Reduce(tmp,sum,count,MPI_LONG_DOUBLE,MPI_SUM,root_leader(c,root),c->leaders));
    if(c->leader_world==c->leader_for_rank[root]) {
        CHECK(MPI_Bcast(sum,count,MPI_LONG_DOUBLE,0,c->local));
        if(c->rank==root) memcpy(out,sum,(size_t)count*sizeof(long double));
    }
    free(tmp);free(sum);return MPI_SUCCESS;
}
int rv_allgather(rv_context *c,const long double *in,int count,long double *out) {
    if(count<0 || count>INT_MAX/c->size) return MPI_ERR_COUNT;
    int *ranks=alloc(c,(size_t)c->local_size*sizeof(int));
    long double *local=alloc(c,(size_t)c->local_size*count*sizeof(long double));
    CHECK(MPI_Gather(&c->rank,1,MPI_INT,ranks,1,MPI_INT,0,c->local));
    CHECK(MPI_Gather(in,count,MPI_LONG_DOUBLE,local,count,MPI_LONG_DOUBLE,0,c->local));
    if(c->local_rank==0) {
        int nodes;CHECK(MPI_Comm_size(c->leaders,&nodes));
        int *counts=alloc(c,(size_t)nodes*sizeof(int)),*offsets=alloc(c,(size_t)nodes*sizeof(int));
        int *vcounts=alloc(c,(size_t)nodes*sizeof(int)),*voffsets=alloc(c,(size_t)nodes*sizeof(int));
        int *allranks=alloc(c,(size_t)c->size*sizeof(int));
        long double *flat=alloc(c,(size_t)c->size*count*sizeof(long double));
        CHECK(MPI_Allgather(&c->local_size,1,MPI_INT,counts,1,MPI_INT,c->leaders));
        for(int i=0,pos=0;i<nodes;i++) {
            offsets[i]=pos;vcounts[i]=counts[i]*count;voffsets[i]=pos*count;pos+=counts[i];
        }
        CHECK(MPI_Allgatherv(ranks,c->local_size,MPI_INT,allranks,counts,offsets,MPI_INT,c->leaders));
        CHECK(MPI_Allgatherv(local,c->local_size*count,MPI_LONG_DOUBLE,flat,vcounts,voffsets,MPI_LONG_DOUBLE,c->leaders));
        for(int i=0;i<c->size;i++) memcpy(out+(size_t)allranks[i]*count,flat+(size_t)i*count,(size_t)count*sizeof(long double));
        free(counts);free(offsets);free(vcounts);free(voffsets);free(allranks);free(flat);
    }
    CHECK(MPI_Bcast(out,c->size*count,MPI_LONG_DOUBLE,0,c->local));
    free(ranks);free(local);return MPI_SUCCESS;
}
void rv_compute(const long double *in,long double *out,int count,int work) {
    for(int i=0;i<count;i++) {
        long double x=in[i];
        for(int j=0;j<work;j++) x=x*1.00000001L+0.00000003L;
        out[i]=x;
    }
}
int rv_fused_sum(rv_context *c,const long double *in,long double *out,int count,int chunk,int work) {
    if(count<0 || chunk<=0 || work<0) return MPI_ERR_ARG;
    long double *send=alloc(c,(size_t)count*sizeof(long double));
    MPI_Request pending[2]={MPI_REQUEST_NULL,MPI_REQUEST_NULL};
    int index=0;
    for(int offset=0;offset<count;offset+=chunk,index++) {
        int slot=index%2,n=(count-offset<chunk)?count-offset:chunk;
        /* Outstanding send/receive regions are never modified or reused. */
        if(pending[slot]!=MPI_REQUEST_NULL) CHECK(MPI_Wait(&pending[slot],MPI_STATUS_IGNORE));
        rv_compute(in+offset,send+offset,n,work);
        CHECK(MPI_Iallreduce(send+offset,out+offset,n,MPI_LONG_DOUBLE,MPI_SUM,c->world,&pending[slot]));
        int other=1-slot,done;
        if(pending[other]!=MPI_REQUEST_NULL) CHECK(MPI_Test(&pending[other],&done,MPI_STATUS_IGNORE));
    }
    CHECK(MPI_Waitall(2,pending,MPI_STATUSES_IGNORE));
    free(send);return MPI_SUCCESS;
}

