# Docker deployment

Build from the repository root:

```sh
docker build -f packaging/docker/Dockerfile.edge -t ps2servers-edge .
docker run --rm --name ps2-edge \
  -p 62966:62966/udp -p 62967:62967/udp \
  -v /path/to/games:/games:ro ps2servers-edge
```

Both UDP ports are fixed so Docker can publish them. Override `PORT`,
`DATA_PORT`, `PROTOCOL_MODE`, `PEER_TIMEOUT`, and `LOG_FORMAT` with environment
variables. The image contains only the static executable and runs as numeric
non-root user `65532`.
