// Xtream-compatible /get.php endpoint.
// Serves the Tuliprox-generated playlist.m3u to credentialed clients only.
export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  const user = env.CLIENT_USER;
  const pass = env.CLIENT_PASS;
  if (!user || !pass) {
    return new Response("500 Server Misconfigured (CLIENT_USER/CLIENT_PASS not set)", { status: 500 });
  }

  const u = url.searchParams.get("username") || url.searchParams.get("user");
  const p = url.searchParams.get("password") || url.searchParams.get("pass") || url.searchParams.get("token");

  if (u !== user || p !== pass) {
    return new Response("401 Unauthorized", {
      status: 401,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  const assetUrl = new URL("/playlist.m3u", url.origin);
  const upstream = await env.ASSETS.fetch(assetUrl);

  if (!upstream.ok) {
    return new Response("503 Playlist not built yet", { status: 503 });
  }

  const body = await upstream.text();
  const type = url.searchParams.get("type") || "m3u_plus";

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": type.includes("m3u") ? "audio/x-mpegurl; charset=utf-8" : "text/plain; charset=utf-8",
      "Content-Disposition": 'inline; filename="playlist.m3u"',
      "Cache-Control": "public, max-age=900",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
