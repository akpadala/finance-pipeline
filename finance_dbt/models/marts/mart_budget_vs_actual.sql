-- The "budget planner" payoff: for every month and category that has a
-- budget target (see seeds/category_budgets.csv), how much did you
-- actually spend, how far over/under budget were you, and by what
-- percent. Powers the dashboard's budget-vs-actual view directly.

with actual as (

    select
        f.transaction_month as month,
        dc.category,
        sum(f.amount_abs) as actual_spent

    from {{ ref('fct_transactions') }} f
    join {{ ref('dim_category') }} dc on f.category_id = dc.category_id
    where f.is_income = false
    group by 1, 2

),

budget as (

    select category, monthly_budget
    from {{ ref('dim_category') }}
    where monthly_budget is not null

),

final as (

    select
        a.month,
        a.category,
        b.monthly_budget                                     as budgeted_amount,
        round(a.actual_spent, 2)                              as actual_spent,
        round(a.actual_spent - b.monthly_budget, 2)           as variance,
        round(100.0 * a.actual_spent / nullif(b.monthly_budget, 0), 1) as pct_of_budget,
        a.actual_spent > b.monthly_budget                    as is_over_budget

    from actual a
    join budget b on a.category = b.category

)

select * from final
order by month, category
