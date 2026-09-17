-- Fact table: the grain is one row per transaction (same grain as the
-- source). This is the hub everything else joins to the two dimensions
-- through. Income rows have a null merchant_id by design -- see
-- dim_merchant.sql, which excludes income entirely since a paycheck
-- deposit isn't a "merchant."

with transactions as (

    select * from {{ ref('int_transactions_categorized') }}

),

final as (

    select
        t.transaction_id,
        t.transaction_date,
        date_trunc('month', t.transaction_date) as transaction_month,

        dc.category_id,
        dm.merchant_id,

        t.amount,
        t.amount_abs,
        t.is_income,
        t.is_likely_recurring,
        t.bank_category as source_bank_category

    from transactions t
    left join {{ ref('dim_category') }} dc on t.category = dc.category
    left join {{ ref('dim_merchant') }} dm on t.merchant_name = dm.merchant_name

)

select * from final
