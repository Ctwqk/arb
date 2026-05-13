FROM rust:1.87-bookworm AS build

WORKDIR /work

COPY Cargo.toml .
COPY rust ./rust

RUN cargo build --release -p strategy-rs

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /work/target/release/strategy-rs /usr/local/bin/strategy-rs

CMD ["strategy-rs"]
