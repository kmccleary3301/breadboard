/** Write one native worker RPC frame through the stdout backpressure boundary. */
export async function writeFrame(value: Record<string, unknown>): Promise<void> {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  const frame = new Uint8Array(4 + encoded.length);
  new DataView(frame.buffer).setUint32(0, encoded.length, false);
  frame.set(encoded, 4);
  await new Promise<void>((resolve, reject) => {
    process.stdout.write(frame, (error) => error ? reject(error) : resolve());
  });
}
