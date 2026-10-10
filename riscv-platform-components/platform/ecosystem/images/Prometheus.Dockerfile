ARG BASE_IMAGE
FROM $BASE_IMAGE
COPY prometheus /usr/local/bin/prometheus
COPY promtool /usr/local/bin/promtool
USER 65534
ENTRYPOINT ["/usr/local/bin/prometheus"]

