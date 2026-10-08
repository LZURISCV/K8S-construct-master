#!/usr/bin/env bash
set -euo pipefail
: "${RCS_MPI_HOSTS:?comma-separated IPv4 worker addresses required}"
: "${RCS_MPI_RANKS:=2}"
: "${RCS_MPI_RANKS_PER_NODE:=2}"
: "${RCS_MPI_TEST:=broadcast}"
: "${RCS_MPI_ELEMENTS:=1048576}"
: "${RCS_MPI_LOOPS:=2000000}"
: "${RCS_MPI_ROUNDS:=5}"
source /opt/rcs/mpi-env.sh
mpirun --version | grep -q 'Open MPI) 4\.' || { echo 'This launcher requires Open MPI 4.x' >&2; exit 2; }
IFS=',' read -r -a hosts <<< "$RCS_MPI_HOSTS"
[[ "$RCS_MPI_RANKS_PER_NODE" =~ ^[1-9][0-9]*$ && "$RCS_MPI_RANKS" =~ ^[1-9][0-9]*$ ]] || { echo 'Positive rank counts required' >&2; exit 1; }
(( RCS_MPI_RANKS == ${#hosts[@]} * RCS_MPI_RANKS_PER_NODE )) || { echo 'Rank count does not match hosts * ranks per node' >&2; exit 1; }
host_slots=''
for host in "${hosts[@]}"; do host_slots+="${host_slots:+,}$host:$RCS_MPI_RANKS_PER_NODE"; done
ssh_args=(-p 2222 -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
for host in "${hosts[@]}"; do
  ready=0
  for attempt in $(seq 1 60); do
    if ssh "${ssh_args[@]}" "root@$host" /opt/rcs/mpi-info.sh >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
  done
  [[ $ready -eq 1 ]] || { echo "MPI worker SSH unavailable: $host:2222" >&2; exit 1; }
done
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
extra=()
if [[ -n ${RCS_MPI_INTERFACE:-} ]]; then
  extra+=(--mca btl_tcp_if_include "$RCS_MPI_INTERFACE" --mca oob_tcp_if_include "$RCS_MPI_INTERFACE")
fi
program="/opt/rcs/mpi-$RCS_MPI_TEST"
program_args=()
if [[ "$RCS_MPI_TEST" == overlap ]]; then
  program=/opt/rcs/mpi-overlap
  program_args=("$RCS_MPI_ELEMENTS" "$RCS_MPI_LOOPS" "$RCS_MPI_ROUNDS")
elif [[ "$RCS_MPI_TEST" != broadcast && "$RCS_MPI_TEST" != reduce && "$RCS_MPI_TEST" != allgather ]]; then
  echo 'Unknown MPI test' >&2; exit 1
fi
# Force SSH even for the local host so all ranks execute inside worker containers.
# Disable shared-memory BTL: workers have separate IPC namespaces and use host TCP.
exec mpirun --prefix "$RCS_MPI_PREFIX" --allow-run-as-root --oversubscribe --bind-to none --map-by "ppr:$RCS_MPI_RANKS_PER_NODE:node" \
  --mca plm rsh --mca plm_rsh_force_rsh 1 --mca plm_rsh_no_tree_spawn 1 \
  --mca plm_rsh_args '-p 2222 -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' \
  --mca btl self,tcp "${extra[@]}" --host "$host_slots" -np "$RCS_MPI_RANKS" "$program" "${program_args[@]}"
