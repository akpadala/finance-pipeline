-- Merchant dimension: one row per distinct merchant, with lifetime stats.
-- Excludes income (paycheck deposits aren't really a "merchant").

with merchant_stats as (

    select
        merchant_name,
        count(*)                                                  as transaction_count,
        sum(amount_abs)                                           as lifetime_spend,
        min(transaction_date)                                     as first_seen_date,
        max(transaction_date)                                     as last_seen_date,
        bool_or(is_likely_recurring)                              as is_recurring_merchant
    from {{ ref('int_transactions_categorized') }}
    where is_income = false
    group by merchant_name

),

-- A merchant should map to exactly one category (our rules key off the
-- merchant name itself), but we resolve to "the most common one" as a
-- safety net rather than assuming that's always true.
merchant_category as (

    select
        merchant_name,
        category,
        row_number() over (
            partition by merchant_name
            order by count(*) desc
        ) as rn
    from {{ ref('int_transactions_categorized') }}
    where is_income = false
    group by merchant_name, category

),

final as (

    select
        md5(m.merchant_name) as merchant_id,
        m.merchant_name,
        c.category,
        m.transaction_count,
        round(m.lifetime_spend, 2) as lifetime_spend,
        m.first_seen_date,
        m.last_seen_date,
        m.is_recurring_merchant

    from merchant_stats m
    join merchant_category c
        on m.merchant_name = c.merchant_name
        and c.rn = 1

)

select * from final
