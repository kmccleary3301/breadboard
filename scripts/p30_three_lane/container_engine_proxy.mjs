import net from "node:net";

const upstreamHost = process.env.BREADBOARD_PROOF_ENGINE_HOST ?? "host.docker.internal";
const upstreamPort = Number(process.env.BREADBOARD_PROOF_ENGINE_PORT ?? "9099");
const listenPort = Number(process.env.BREADBOARD_PROOF_PROXY_PORT ?? "9099");

const server = net.createServer((client) => {
  const upstream = net.connect(upstreamPort, upstreamHost);
  const close = () => {
    client.destroy();
    upstream.destroy();
  };

  client.pipe(upstream);
  upstream.pipe(client);
  client.on("error", close);
  upstream.on("error", close);
});

server.listen(listenPort, "127.0.0.1");
