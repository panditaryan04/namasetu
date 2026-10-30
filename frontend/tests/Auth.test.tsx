import { afterEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../src/services/api";
import { tokens } from "../src/services/auth";

afterEach(() => {
  vi.restoreAllMocks();
  tokens.clear();
});

describe("token refresh", () => {
  it("is single-flight: concurrent 401s share one /auth/refresh call", async () => {
    sessionStorage.setItem("namasetu.refresh", "r1");
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve(
                new Response(
                  JSON.stringify({
                    access_token: "a2",
                    refresh_token: "r2",
                    token_type: "bearer",
                    expires_in: 900,
                    user: {
                      username: "viewer",
                      display_name: "V",
                      role: "viewer",
                      permissions: ["read"],
                      auth_method: "password",
                    },
                  }),
                  { status: 200 },
                ),
              ),
            10,
          ),
        ),
    );
    const results = await Promise.all([tokens.tryRefresh(), tokens.tryRefresh(), tokens.tryRefresh()]);
    expect(results).toEqual([true, true, true]);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(tokens.access()).toBe("a2");
    await tokens.tryRefresh();
    expect(spy).toHaveBeenCalledTimes(2); // a later refresh is a new request
  });

  it("after a page reload, refreshes before the first API call instead of collecting a 401", async () => {
    sessionStorage.setItem("namasetu.refresh", "r1"); // refresh token survives the reload; access token does not
    const calls: { url: string; auth: string | null }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      calls.push({ url, auth: new Headers(init?.headers).get("Authorization") });
      const body = url.endsWith("/auth/refresh")
        ? {
            access_token: "a2",
            refresh_token: "r2",
            token_type: "bearer",
            expires_in: 900,
            user: {
              username: "viewer",
              display_name: "V",
              role: "viewer",
              permissions: ["read"],
              auth_method: "password",
            },
          }
        : { ok: true };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    await apiGet("/dashboard");
    expect(calls.map((c) => c.url.replace(/^.*\/api/, ""))).toEqual(["/auth/refresh", "/dashboard"]);
    expect(calls[1]!.auth).toBe("Bearer a2");
  });
});
