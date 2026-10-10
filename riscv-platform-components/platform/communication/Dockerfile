ARG BASE_IMAGE
FROM $BASE_IMAGE
USER root
RUN dnf install -y gcc make openmpi openmpi-devel openssh-server openssh-clients python3 iproute iperf3 procps-ng which && dnf clean all
COPY . /opt/rv-communication
RUN bash /opt/rv-communication/build.sh && cp /opt/rv-communication/bin/rv-bench /usr/local/bin/rv-bench && mkdir -p /run/sshd /root/.ssh && ssh-keygen -A && printf '\nPermitRootLogin prohibit-password\nStrictModes no\n' >> /etc/ssh/sshd_config
ENV PATH="/usr/lib64/openmpi/bin:/usr/lib/openmpi/bin:$PATH"
ENV OMPI_ALLOW_RUN_AS_ROOT=1
ENV OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
CMD ["/usr/sbin/sshd", "-D", "-e"]

