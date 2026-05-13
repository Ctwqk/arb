FROM rust:1.87-bookworm AS build

WORKDIR /work

COPY Cargo.toml .
COPY rust ./rust

RUN cargo build --release -p collector-rs

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /work/target/release/collector-rs /usr/local/bin/collector-rs

CMD ["collector-rs"]
