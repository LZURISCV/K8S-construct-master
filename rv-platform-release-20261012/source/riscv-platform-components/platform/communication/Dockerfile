ARG BASE_IMAGE
FROM $BASE_IMAGE
USER root
ENTRYPOINT []
RUN set -eu; \
    if command -v dnf >/dev/null 2>&1; then \
        dnf install -y gcc make openmpi openmpi-devel openssh-server openssh-clients python3 iproute iperf3 procps-ng which ca-certificates; \
        dnf clean all; \
    elif command -v apt-get >/dev/null 2>&1; then \
        rv_mpi_packages=; \
        if ! command -v mpicc >/dev/null 2>&1 || ! command -v mpirun >/dev/null 2>&1; then \
            rv_mpi_packages='openmpi-bin libopenmpi-dev'; \
        fi; \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc make openssh-server openssh-client python3 iproute2 iperf3 procps ca-certificates $rv_mpi_packages; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        echo 'BASE_IMAGE must provide dnf or apt-get and repositories for linux/riscv64' >&2; \
        exit 1; \
    fi
COPY . /opt/rv-communication
RUN bash /opt/rv-communication/build.sh && cp /opt/rv-communication/bin/rv-bench /usr/local/bin/rv-bench && mkdir -p /run/sshd /root/.ssh && ssh-keygen -A && printf '\nPermitRootLogin prohibit-password\nStrictModes no\n' >> /etc/ssh/sshd_config
ENV PATH="/usr/lib64/openmpi/bin:/usr/lib/openmpi/bin:$PATH"
ENV OMPI_ALLOW_RUN_AS_ROOT=1
ENV OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
CMD ["/usr/sbin/sshd", "-D", "-e"]

