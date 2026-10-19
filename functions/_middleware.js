export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  if (url.pathname === "/playlist.m3u") {
    const clientUser = env.CLIENT_USER || "admin";
    const clientPass = env.CLIENT_PASS || "tuliprox2026";

    const username = url.searchParams.get("username") || url.searchParams.get("user");
    const password = url.searchParams.get("password") || url.searchParams.get("pass") || url.searchParams.get("token");

    if (!username || !password || username !== clientUser || password !== clientPass) {
      return new Response("401 Unauthorized - Direct M3U Access Requires Credentials", {
        status: 401,
        headers: { "Content-Type": "text/plain" }
      });
    }
  }

  return next();
}
