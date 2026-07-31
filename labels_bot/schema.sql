create table if not exists public.label_orders (
    id text primary key,
    telegram_user_id bigint not null,
    telegram_username text,
    customer_name text,
    quantity integer not null check (quantity > 0),
    unit_price numeric(10,2) not null default 25.00,
    total numeric(10,2) not null,
    payment_method text not null default 'binance_pay',
    status text not null default 'pending_payment_review',
    label_files jsonb not null default '[]'::jsonb,
    receipt_file jsonb not null default '{}'::jsonb,
    status_history jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists label_orders_user_id_idx
    on public.label_orders (telegram_user_id);

create index if not exists label_orders_status_idx
    on public.label_orders (status);

create table if not exists public.label_tickets (
    id text primary key,
    telegram_user_id bigint not null,
    telegram_username text,
    customer_name text,
    category text not null,
    status text not null default 'open',
    messages jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists label_tickets_user_id_idx
    on public.label_tickets (telegram_user_id);

create index if not exists label_tickets_status_idx
    on public.label_tickets (status);

alter table public.label_orders enable row level security;
alter table public.label_tickets enable row level security;

-- El bot usa exclusivamente la clave service_role guardada como secreto en Render.
-- No creamos políticas públicas; la clave service_role puede acceder desde el servidor.
