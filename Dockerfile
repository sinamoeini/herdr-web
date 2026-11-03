FROM rust:1.99.1-bookworm@sha256:9181b56c2a21ab1d5e73cffe04fc762426d30aa7fac3c91141dd033f49a7fc8b AS build
ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 || { echo 'Build with --platform linux/amd64' >&2; exit 1; }
WORKDIR /src
COPY Cargo.toml Cargo.lock rust-toolchain.toml build.sh ./
COPY server ./server
COPY public ./public
RUN ./build.sh

FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash ca-certificates curl git openssh-client procps python3 tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /bin/bash herdr \
    && mkdir -p /usr/local/share/licenses/herdr /usr/local/share/doc/herdr-web \
    && curl --fail --show-error --location --retry 3 \
        https://github.com/herdrdev/herdr/releases/download/v0.9.5/herdr-linux-x86_64 \
        --output /usr/local/bin/herdr \
    && printf '%s  %s\n' \
        14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d /usr/local/bin/herdr \
        | sha256sum --check \
    && chmod 755 /usr/local/bin/herdr \
    && curl --fail --show-error --location --retry 3 \
        https://raw.githubusercontent.com/herdrdev/herdr/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/LICENSE \
        --output /usr/local/share/licenses/herdr/LICENSE \
    && printf '%s  %s\n' \
        c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4 /usr/local/share/licenses/herdr/LICENSE \
        | sha256sum --check \
    && curl --fail --show-error --location --retry 3 \
        https://raw.githubusercontent.com/herdrdev/herdr/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/vendor/libghostty-vt/LICENSE \
        --output /usr/local/share/licenses/herdr/ghostty-LICENSE \
    && curl --fail --show-error --location --retry 3 \
        https://raw.githubusercontent.com/herdrdev/herdr/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/vendor/libghostty-vt/pkg/afl%2B%2B/LICENSE \
        --output /usr/local/share/licenses/herdr/afl-LICENSE \
    && curl --fail --show-error --location --retry 3 \
        https://raw.githubusercontent.com/herdrdev/herdr/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/vendor/portable-pty/LICENSE.md \
        --output /usr/local/share/licenses/herdr/portable-pty-LICENSE \
    && printf '%s\n' \
        '386211873e5b7a02f663ae4d7adf96285999f91608f8f9f31fecfd0f4095e6f1  /usr/local/share/licenses/herdr/ghostty-LICENSE' \
        '337a40d58de145036691382b3aec220ee28f507fe4f0db8970b6844f6dd60eb3  /usr/local/share/licenses/herdr/afl-LICENSE' \
        '191c46fcf52061382b1c51a70311eb9081381cc158e5899f3739473a9432185b  /usr/local/share/licenses/herdr/portable-pty-LICENSE' \
        | sha256sum --check
COPY --from=build /src/target/release/herdr-web /usr/local/bin/herdr-web
COPY tools/docker-entrypoint.py /usr/local/lib/herdr-web/docker-entrypoint.py
COPY LICENSE THIRD_PARTY_NOTICES.md PORT.md /usr/local/share/doc/herdr-web/
ENV HOME=/home/herdr \
    SHELL=/bin/bash \
    HERDR_BIN=/usr/local/bin/herdr \
    HERDR_SESSION=default \
    HERDR_WEB_BIND=0.0.0.0 \
    PORT=8000
USER herdr
WORKDIR /home/herdr
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl --fail --silent --max-time 2 "http://127.0.0.1:${PORT}/api/state" > /dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/usr/local/lib/herdr-web/docker-entrypoint.py"]
CMD []
