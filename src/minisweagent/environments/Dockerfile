FROM ubuntu:22.04
RUN apt-get update && apt-get install -y python3 python3-pip git openjdk-17-jdk-headless android-sdk
WORKDIR /project

# Copy Android SDK licenses
COPY ./docker_files/licenses /usr/lib/android-sdk/licenses
# Install and build NullAway dependency
RUN git clone https://github.com/nimakarimipour/NullAway.git /project/NullAway && cd /project/NullAway && git checkout d0c7d8390964ee5dc95d6bf93ae76b85913b2342 \
    && echo 'sdk.dir=/usr/lib/android-sdk' >local.properties && ./gradlew build -x test && ./gradlew publishToMavenLocal

# Install and build NullAwayAnnotatorBaseline dependency
RUN git clone https://github.com/Pascal-Joos/NullAwayAnnotatorBaseline.git /project/NullAwayAnnotatorBaseline && cd /project/NullAwayAnnotatorBaseline && git checkout -b joos/auto-code-fix-baseline origin/joos/auto-code-fix-baseline \
    && ./gradlew build -x test && ./gradlew publishToMavenLocal



ENV JAVA_HOME=/usr/lib/jvm/java-1.17.0-openjdk-amd64
ENV ANDROID_HOME=/usr/lib/android-sdk

CMD ["bash"]