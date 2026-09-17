-- Intermediate: applies business logic (categorization + a cleaned
-- merchant name + a recurring-charge flag) on top of staging. Nothing
-- here changes the grain -- still one row per transaction -- so it's a
-- view, not a table. The marts layer is what actually aggregates.

with stg as (

    select * from {{ ref('stg_transactions') }}

),

categorized as (

    select
        transaction_id,
        transaction_date,
        post_date,
        description,

        -- strip trailing store numbers / codes like "#112" or "*4821" so
        -- "TRADER JOE'S #112" and "TRADER JOE'S #048" both roll up to one
        -- merchant instead of looking like different merchants.
        trim(regexp_replace(description, '[#*][A-Za-z0-9]*\s*$', '')) as merchant_name,

        {{ categorize_transaction('description') }} as category,

        bank_category,
        transaction_type,
        amount,
        abs(amount) as amount_abs,
        is_income,
        memo,
        _source_file

    from stg

),

flagged as (

    select
        *,

        -- Recurring-charge heuristic: this exact merchant + this exact
        -- amount has shown up in 3+ distinct months. That catches fixed
        -- subscriptions (Netflix, rent, Spotify) without hardcoding a
        -- merchant allowlist. It's intentionally simple -- see the note
        -- in categorize_transaction.sql about where rule-based logic like
        -- this tends to hit its ceiling, and how the README's Phase 2
        -- proposes tightening it.
        count(distinct date_trunc('month', transaction_date))
            over (partition by merchant_name, amount) >= 3 as is_likely_recurring

    from categorized

)

select * from flagged
