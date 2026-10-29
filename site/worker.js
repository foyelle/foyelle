// foyelle.org is static files; this runs only for /media/* (wrangler.jsonc, run_worker_first). Static
// assets don't answer byte ranges, and Safari plays an MP4 only from 206 Partial Content replies.
export default {
  async fetch(request, env) {
    const asset = await env.ASSETS.fetch(request);
    const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("Range") ?? "");
    if (!range || asset.status !== 200) return withRanges(asset);
    const body = await asset.arrayBuffer();
    const size = body.byteLength;
    let start = range[1] === "" ? size - Number(range[2]) : Number(range[1]);
    let end = range[1] === "" || range[2] === "" ? size - 1 : Math.min(Number(range[2]), size - 1);
    if (!(start >= 0 && start <= end && end < size)) {
      return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    }
    const headers = new Headers(asset.headers);
    headers.set("Accept-Ranges", "bytes");
    headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
    headers.set("Content-Length", String(end - start + 1));
    return new Response(body.slice(start, end + 1), { status: 206, headers });
  },
};

function withRanges(response) {
  const headers = new Headers(response.headers);
  headers.set("Accept-Ranges", "bytes");
  return new Response(response.body, { status: response.status, headers });
}
