import { useEffect, useState } from "react";
import { tokens, type UserInfo } from "./auth";

/** Current user (null when signed out); re-renders on login, refresh and logout. */
export function useAuth(): UserInfo | null {
  const [user, setUser] = useState<UserInfo | null>(tokens.user);
  useEffect(() => tokens.subscribe(setUser), []);
  return user;
}
