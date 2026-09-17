-- Staging: 1:1 with the raw source, renamed/typed consistently, plus a
-- stable surrogate key. This is the only model allowed to reference the
-- source() macro directly -- everything downstream builds on this model
-- via ref(), so if the raw table's shape ever changes, there's exactly
-- one place to fix it.

with source as (

    select * from {{ source('raw', 'transactions') }}

),

renamed as (

    select
        -- surrogate key: hash of the fields that make a row unique. Bank
        -- exports don't give us a natural transaction id, so we make one.
        md5(
            cast(transaction_date as varchar) || '|' ||
            description || '|' ||
            cast(amount as varchar) || '|' ||
            _source_file || '|' ||
            cast(row_number() over (
                partition by transaction_date, description, amount, _source_file
                order by post_date
            ) as varchar)
        ) as transaction_id,

        transaction_date,
        post_date,
        trim(description)            as description,
        nullif(trim(category), '')   as bank_category,   -- blank strings -> null
        type                          as transaction_type,
        amount,
        nullif(trim(memo), '')       as memo,

        amount > 0                    as is_income,

        _source_file,
        _loaded_at

    from source

)

select * from renamed
