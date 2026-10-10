ARG BASE_IMAGE
FROM $BASE_IMAGE
USER root
RUN dnf install -y ca-certificates fontconfig && dnf clean all && mkdir -p /var/lib/grafana /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards && chown -R 472:472 /var/lib/grafana
COPY grafana /usr/local/bin/grafana
COPY public /usr/share/grafana/public
COPY conf /usr/share/grafana/conf
ENV GF_PATHS_HOME=/usr/share/grafana
ENV GF_PATHS_DATA=/var/lib/grafana
ENV GF_PATHS_PROVISIONING=/etc/grafana/provisioning
USER 472
ENTRYPOINT ["/usr/local/bin/grafana", "server"]

