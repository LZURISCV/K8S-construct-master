ARG BASE_IMAGE
FROM $BASE_IMAGE
USER root
RUN dnf install -y iptables iproute procps-ng util-linux ca-certificates && dnf clean all && mkdir -p /etc/istio/proxy /var/lib/istio /usr/local/bin && chmod 0777 /etc/istio/proxy /var/lib/istio
COPY pilot-agent /usr/local/bin/pilot-agent
COPY envoy /usr/local/bin/envoy
ENTRYPOINT ["/usr/local/bin/pilot-agent"]

