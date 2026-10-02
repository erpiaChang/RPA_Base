// 대시보드 설정. Supabase 프로젝트 URL 과 **공개(anon) 키**만 둔다 (읽기는 로그인 + RLS).
// ★ git 에는 자리표시만 둔다 (10-02 사용자 결정). 실제 값은 .env 의 SUPABASE_URL·SUPABASE_PUBLISHABLE_KEY —
//   `tools/build_web` 이 build/web/ 에 바꿔 쓰고 그 폴더를 올린다 (deploy_web.bat). index.html·_headers 의 CSP 도 같다.
// 비밀 키(서버 전용 키)는 절대 여기 두지 않는다 (`.claude/hooks/guard_secrets.py` 가 막는다).
window.RPA_CONFIG = {
  url: "__SUPABASE_URL__", // https://<프로젝트 id>.supabase.co  (뒤의 /rest/v1/ 는 뗀다)
  anonKey: "__SUPABASE_PUBLISHABLE_KEY__", // Project Settings → API Keys → Publishable key (sb_publishable_...)
  retentionDays: 180,
};
