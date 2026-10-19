export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  const clientUser = env.CLIENT_USER || "admin";
  const clientPass = env.CLIENT_PASS || "tuliprox2026";

  const username = url.searchParams.get("username") || url.searchParams.get("user");
  const password = url.searchParams.get("password") || url.searchParams.get("pass") || url.searchParams.get("token");

  // Check auth
  if (!username || !password || username !== clientUser || password !== clientPass) {
    return new Response("401 Unauthorized - Invalid Xtream Credentials", {
      status: 401,
      headers: { "Content-Type": "text/plain" }
    });
  }

  // Fetch static playlist.m3u asset from ASSETS or origin
  const playlistUrl = new URL("/playlist.m3u", url.origin);
  const assetResponse = await env.ASSETS.fetch(playlistUrl);

  if (!assetResponse.ok) {
    return new Response("#EXTM3U\n#EXTINF:-1,Sample Stream\nhttp://example.com/stream.m3u8", {
      status: 200,
      headers: {
        "Content-Type": "audio/x-mpegurl; charset=utf-8",
        "Access-Control-Allow-Origin": "*"
      }
    });
  }

  const content = await assetResponse.text();
  return new Response(content, {
    status: 200,
    headers: {
      "Content-Type": "audio/x-mpegurl; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=3600"
    }
  });
}
