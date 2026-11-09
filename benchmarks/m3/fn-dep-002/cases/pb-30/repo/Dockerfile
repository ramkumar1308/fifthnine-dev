FROM eclipse-temurin:21-jre
COPY target/app-0.0.1.jar /app/app.jar
WORKDIR /app
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75"
ENTRYPOINT java $JAVA_OPTS -jar /app/app.jar
