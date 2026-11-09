# Theta Terminal (ThetaData v3) for Render. UNVERIFIED: written from ThetaData's
# Getting Started page; it has not been run, because no subscription exists yet.
# Confirm on first boot (see docs/RUNBOOK.md, "ThetaData"):
#   1. The default config the terminal writes binds 127.0.0.1. Other Render
#      services can only reach it if `host` is 0.0.0.0. The config filename and
#      layout are not documented, so read the generated file and adjust it.
#   2. Error 476 WRONG_IP means the terminal pins the first client IP. Only one
#      client (OptionsCoach web, or the worker) should call it.
#   3. Memory: ThetaData publishes no JVM sizing; start at -Xmx384m and watch it.
# Credentials: THETADATA_API_KEY is set as a Render secret and read from the
# environment by the terminal (documented, terminal build 20260615 or later).
FROM eclipse-temurin:21-jre
RUN useradd --system --uid 10002 --create-home --home-dir /opt/theta theta
WORKDIR /opt/theta
# Official download location (thetadata.net/docs Getting Started). The jar
# updates itself at startup, so this pins nothing; no published checksum exists.
ADD --chown=theta https://downloads.thetadata.us/ThetaTerminalv3.jar /opt/theta/ThetaTerminalv3.jar
USER theta
ENV JAVA_TOOL_OPTIONS="-Xmx384m"
EXPOSE 25503
CMD ["java", "-jar", "/opt/theta/ThetaTerminalv3.jar"]
