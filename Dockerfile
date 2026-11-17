FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    gobuster \
    nikto \
    sqlmap \
    hydra \
    john \
    whatweb \
    dirb \
    seclists \
    netcat-traditional \
    dnsutils \
    iputils-ping \
    iproute2 \
    curl \
    wget \
    jq \
    openvpn \
    python3 \
    python3-pip \
    git \
    vim \
    less \
    unzip \
    && apt-get install -y --no-install-recommends wordlists \
    && rm -rf /var/lib/apt/lists/* \
    && gunzip -k /usr/share/wordlists/rockyou.txt.gz

COPY agent/requirements.txt /agent/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /agent/requirements.txt

COPY agent/ /agent/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
