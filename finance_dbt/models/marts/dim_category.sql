-- Category dimension: every category that shows up in the data, grouped
-- into a rough Needs/Wants/Income split (the classic 50/30/20 budgeting
-- framework) and joined to your monthly budget target from the seed.

with categories as (

    select distinct category
    from {{ ref('int_transactions_categorized') }}

),

budgets as (

    select * from {{ ref('category_budgets') }}

),

final as (

    select
        md5(c.category) as category_id,
        c.category,

        case
            when c.category in ('Groceries', 'Rent', 'Utilities & Bills', 'Transportation', 'Healthcare')
                then 'Needs'
            when c.category in ('Dining & Coffee', 'Subscriptions', 'Shopping', 'Entertainment', 'Travel')
                then 'Wants'
            when c.category = 'Income'
                then 'Income'
            else 'Other'
        end as category_group,

        b.monthly_budget

    from categories c
    left join budgets b on c.category = b.category

)

select * from final
