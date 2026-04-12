FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY go.mod ./
COPY go.sum* ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -o spine .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates git
COPY --from=builder /build/spine /usr/local/bin/spine
COPY spine_config.json /spine/spine_config.json
ENTRYPOINT ["/usr/local/bin/spine"]
CMD ["/spine/spine_config.json"]