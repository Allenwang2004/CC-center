-- cc-center: what you wrote, in Supabase.
--
-- Run this once in the Supabase dashboard (SQL Editor); it is safe to run
-- again after an update. Everything computed from transcripts stays on your
-- machines; only journal entries and notes live here, one row each, scoped
-- to the account that wrote them -- plus the pictures pasted into a journal
-- entry, in a private storage bucket at the bottom of this file.
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

-- Pictures pasted into a journal entry. The bucket is private: nothing in it
-- has a public URL. An account reaches the folder named after its own id and
-- nothing else, and only ever through the local server, which signs every
-- request with that account's token.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('cc-images', 'cc-images', false, 10485760,
        array['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
on conflict (id) do update
    set public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "cc-images: own folder" on storage.objects;
create policy "cc-images: own folder"
    on storage.objects
    for all
    to authenticated
    using (bucket_id = 'cc-images' and (storage.foldername(name))[1] = auth.uid()::text)
    with check (bucket_id = 'cc-images' and (storage.foldername(name))[1] = auth.uid()::text);
