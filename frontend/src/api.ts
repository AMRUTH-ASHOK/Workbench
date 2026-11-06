let token = "";
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  if (method !== "GET" && !token)
    token = (await api<{ token: string }>("/session")).token;
  const response = await fetch("/api" + path, {
    method,
    headers:
      method === "GET"
        ? {}
        : { "Content-Type": "application/json", "X-Workbench-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 403) token = "";
    const error = await response
      .json()
      .catch(() => ({
        detail:
          "Workbench is unavailable. Check that the local service is running.",
      }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail),
    );
  }
  return response.json();
}
