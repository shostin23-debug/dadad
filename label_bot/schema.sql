create table if not exists public.label_orders (
    order_id text primary key,
    telegram_user_id bigint not null,
    telegram_username text,
    quantity integer not null check (quantity >= 1 and quantity <= 50),
    total numeric(10,2) not null,
    payment_method text,
    customer_name text,
    contact text,
    receipt_file_id text,
    receipt_file_type text,
    status text not null default 'draft_uploading',
    status_history jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.label_files (
    id bigint generated always as identity primary key,
    order_id text not null references public.label_orders(order_id) on delete cascade,
    sequence integer not null,
    file_id text not null,
    file_unique_id text not null,
    file_type text not null,
    file_name text,
    created_at timestamptz not null default now(),
    unique(order_id, file_unique_id),
    unique(order_id, sequence)
);

create index if not exists label_orders_user_idx
    on public.label_orders(telegram_user_id, created_at desc);

create index if not exists label_orders_status_idx
    on public.label_orders(status, created_at desc);

create index if not exists label_files_order_idx
    on public.label_files(order_id, sequence);

alter table public.label_orders enable row level security;
alter table public.label_files enable row level security;

-- El bot usa exclusivamente la Service Role Key desde Render.
-- No se crean políticas públicas; los clientes nunca acceden directamente a estas tablas.
