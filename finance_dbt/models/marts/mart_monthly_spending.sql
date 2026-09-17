-- Reporting mart: spend and transaction count by month + category. This
-- is the table the dashboard's "spending by category" and "monthly
-- trend" charts read directly -- no joins or aggregation happen at query
-- time in the app, it's all precomputed here.

with monthly as (

    select
        f.transaction_month as month,
        dc.category,
        dc.category_group,
        count(*)                                                         as transaction_count,
        sum(case when f.is_income then 0 else f.amount_abs end)          as total_spent,
        sum(case when f.is_income then f.amount else 0 end)              as total_income

    from {{ ref('fct_transactions') }} f
    join {{ ref('dim_category') }} dc on f.category_id = dc.category_id
    group by 1, 2, 3

)

select * from monthly
order by month, category
