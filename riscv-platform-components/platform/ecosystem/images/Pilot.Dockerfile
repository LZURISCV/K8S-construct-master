ARG BASE_IMAGE
FROM $BASE_IMAGE
COPY pilot-discovery /usr/local/bin/pilot-discovery
USER 1337
ENTRYPOINT ["/usr/local/bin/pilot-discovery"]

