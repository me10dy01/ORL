"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    const role = sessionStorage.getItem("orl_role");
    if (!sessionStorage.getItem("orl_token")) {
      router.push("/login");
      return;
    }
    router.push(role === "agent" ? "/agent" : "/chat");
  }, [router]);
  return <main>正在进入…</main>;
}
