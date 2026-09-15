-- cc-center: what you wrote, in Supabase.
--
-- Run this once in the Supabase dashboard (SQL Editor). Everything computed
-- from transcripts stays on your machines; only journal entries and notes
-- live here, one row each, scoped to the account that wrote them.
--
-- The unique key mirrors the local cache (kind, cwd, host, ref), so a row
-- written on one machine lands in the same place when another machine pulls.

create table if not exists public.entries (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    kind        text not null check (kind in ('journal', 'note')),
    cwd         text not null,                       -- 專案路徑
    host        text not null,                       -- 專案在哪台機器
    ref         text not null,                       -- journal: YYYY-MM-DD; note: YYYY-MM-DD-HHMM[-n]
    day         text not null,                       -- 歸到哪一天
    title       text not null default '',            -- note 專用; journal 的標題就是日期
    body        text not null default '',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (user_id, kind, cwd, host, ref)
);

create index if not exists entries_user_cwd_day on public.entries (user_id, cwd, day);
create index if not exists entries_user_updated on public.entries (user_id, updated_at);

-- updated_at is set here, not by the client, so clocks on different machines
-- never disagree about which copy is newer.
create or replace function public.entries_touch()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists entries_touch on public.entries;
create trigger entries_touch
    before update on public.entries
    for each row execute function public.entries_touch();

-- Row level security: an account sees and edits only its own rows. The anon
-- key alone reaches nothing; every request carries the signed-in user's JWT.
alter table public.entries enable row level security;

drop policy if exists "entries: own rows" on public.entries;
create policy "entries: own rows"
    on public.entries
    for all
    to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.entries to authenticated;
