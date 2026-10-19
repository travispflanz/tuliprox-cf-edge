// Gate direct /playlist.m3u access behind Xtream-style credentials.
export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  if (url.pathname === "/playlist.m3u") {
    if (!authorized(url, env)) {
      return new Response("401 Unauthorized", {
        status: 401,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
    }
  }
  return next();
}

function authorized(url, env) {
  const user = env.CLIENT_USER;
  const pass = env.CLIENT_PASS;
  if (!user || !pass) return false; // fail closed
  const u = url.searchParams.get("username") || url.searchParams.get("user");
  const p = url.searchParams.get("password") || url.searchParams.get("pass") || url.searchParams.get("token");
  return u === user && p === pass;
}
