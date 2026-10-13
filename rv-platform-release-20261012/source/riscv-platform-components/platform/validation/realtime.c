#define _GNU_SOURCE
#include <errno.h>
#include <float.h>
#include <inttypes.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
static volatile long double sink;
static int64_t ns(struct timespec t){return (int64_t)t.tv_sec*1000000000LL+t.tv_nsec;}
static struct timespec stamp(int64_t n){struct timespec t={n/1000000000LL,n%1000000000LL};return t;}
static int compare(const void*a,const void*b){int64_t x=*(const int64_t*)a,y=*(const int64_t*)b;return (x>y)-(x<y);}
static int64_t number(const char *s){char *end;errno=0;int64_t n=strtoll(s,&end,10);if(errno||!*s||*end){fprintf(stderr,"invalid number\n");exit(2);}return n;}
int main(int argc,char **argv){
 if(argc<5){fprintf(stderr,"realtime samples period_us deadline_us work [cpu|-1] [fifo|-1]\n");return 2;}
 int64_t count=number(argv[1]),period_us=number(argv[2]),deadline_us=number(argv[3]),work=number(argv[4]);
 if(period_us<1||deadline_us<1||period_us>1000000000000LL||deadline_us>1000000000000LL){fprintf(stderr,"invalid microseconds\n");return 2;}
 int64_t period=period_us*1000,deadline=deadline_us*1000;
 int cpu=argc>5?(int)number(argv[5]):-1,priority=argc>6?(int)number(argv[6]):-1;
 if(count<1||count>10000000||period<1||deadline<1||work<1||cpu>=CPU_SETSIZE){fprintf(stderr,"invalid range\n");return 2;}
 int64_t *elapsed=calloc((size_t)count,sizeof(int64_t)),*jitter=calloc((size_t)count,sizeof(int64_t)),*sorted=calloc((size_t)count,sizeof(int64_t));
 if(!elapsed||!jitter||!sorted){perror("calloc");return 2;}
 if(cpu>=0){cpu_set_t set;CPU_ZERO(&set);CPU_SET(cpu,&set);if(sched_setaffinity(0,sizeof(set),&set)){perror("affinity");return 2;}}
 if(priority>=0){struct sched_param p={.sched_priority=priority};if(sched_setscheduler(0,SCHED_FIFO,&p)){perror("SCHED_FIFO");return 2;}}
 int locked=getenv("RV_MLOCK")&&strcmp(getenv("RV_MLOCK"),"1")==0;
 if(locked&&mlockall(MCL_CURRENT|MCL_FUTURE)){perror("mlockall");return 2;}
 for(int64_t i=0;i<count;i++)elapsed[i]=jitter[i]=sorted[i]=0;
 struct timespec current;clock_gettime(CLOCK_MONOTONIC,&current);
 int64_t release=ns(current)+100000000LL,misses=0,maximum_jitter=0;
 for(int64_t i=0;i<count;i++,release+=period){
  struct timespec planned=stamp(release);int e;
  do{e=clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&planned,NULL);}while(e==EINTR);
  if(e){fprintf(stderr,"clock_nanosleep: %s\n",strerror(e));return 2;}
  clock_gettime(CLOCK_MONOTONIC,&current);jitter[i]=ns(current)-release;
  if(jitter[i]>maximum_jitter)maximum_jitter=jitter[i];
  long double value=1.0L;
  for(int64_t j=0;j<work;j++)value=value*1.0000000001L+0.000000000003L;
  sink=value;
  clock_gettime(CLOCK_MONOTONIC,&current);elapsed[i]=ns(current)-release;
  if(elapsed[i]>deadline)misses++;
 }
 memcpy(sorted,elapsed,(size_t)count*sizeof(int64_t));qsort(sorted,(size_t)count,sizeof(int64_t),compare);
 int64_t p50=(count+1)/2-1,p99=(count*99+99)/100-1;
 printf("{\"operation\":\"compute-deadline\",\"samples\":%"PRId64",\"period_ns\":%"PRId64",\"deadline_ns\":%"PRId64",\"work\":%"PRId64",\"cpu\":%d,\"fifoPriority\":%d,\"memoryLocked\":%s,\"mantissa_bits\":%d,\"p50_ns\":%"PRId64",\"p99_ns\":%"PRId64",\"max_ns\":%"PRId64",\"max_start_jitter_ns\":%"PRId64",\"deadline_misses\":%"PRId64",\"hardRealtimeGuarantee\":false,\"evidence\":\"real-periodic-compute\",\"samples_ns\":[",
  count,period,deadline,work,cpu,priority,locked?"true":"false",LDBL_MANT_DIG,sorted[p50],sorted[p99],sorted[count-1],maximum_jitter,misses);
 for(int64_t i=0;i<count;i++)printf("%s%"PRId64,i?",":"",elapsed[i]);
 printf("],\"start_jitter_ns\":[");
 for(int64_t i=0;i<count;i++)printf("%s%"PRId64,i?",":"",jitter[i]);
 puts("]}");free(elapsed);free(jitter);free(sorted);return misses?1:0;
}

