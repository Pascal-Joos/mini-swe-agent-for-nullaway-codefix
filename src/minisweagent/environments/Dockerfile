FROM ubuntu:22.04
RUN apt-get update && apt-get install -y python3 python3-pip git openjdk-17-jdk-headless android-sdk
WORKDIR /project

# Copy Android SDK licenses
COPY ./docker_files/licenses /usr/lib/android-sdk/licenses


ENV JAVA_HOME=/usr/lib/jvm/java-1.17.0-openjdk-amd64
ENV ANDROID_HOME=/usr/lib/android-sdk

COPY docker_files/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

CMD ["bash"]