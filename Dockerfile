FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    parted \
    cgpt \
    e2fsprogs \
    util-linux \
    debootstrap \
    cpio \
    gpgv \
    python3 \
    python3-pip \
    rsync \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages click

WORKDIR /shimpy
COPY . .

ENTRYPOINT ["python3", "build.py"]
