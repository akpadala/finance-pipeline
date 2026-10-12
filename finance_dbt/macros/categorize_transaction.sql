{#
  Rule-based transaction categorization, centralized in one macro so the
  same rules are used everywhere they're needed instead of copy-pasted
  across models.

  Design note: the bank's own
  `category` column (see stg_transactions.bank_category) is inconsistent
  -- sometimes blank, sometimes too generic to be useful for budgeting.
  So instead of trusting the source, we derive our own category purely
  from keyword matches against the merchant description. It's simple,
  fully transparent (you can read every rule right here), and gets it
  right for the overwhelming majority of transactions in this dataset.

  Known limitation, and a real one: keyword matching breaks down on
  merchants it's never seen (a new coffee shop, a mom-and-pop store) and
  on genuinely mixed-use merchants (CVS sells both prescriptions and
  snacks). ml/train_categorizer.py addresses this with a small text
  classifier (TF-IDF + logistic regression over merchant descriptions)
  trained on these rules' output, kept separate so it's always clear
  what's rules vs. what's ML.
#}

{% macro categorize_transaction(description_col) %}
case
    when {{ description_col }} ilike '%trader joe%'
      or {{ description_col }} ilike '%whole foods%'
      or {{ description_col }} ilike '%stop & shop%'
        then 'Groceries'

    when {{ description_col }} ilike '%starbucks%'
      or {{ description_col }} ilike '%chipotle%'
      or {{ description_col }} ilike '%sweetgreen%'
      or {{ description_col }} ilike '%doordash%'
      or {{ description_col }} ilike '%uber eats%'
      or {{ description_col }} ilike '%pizza%'
        then 'Dining & Coffee'

    when {{ description_col }} ilike '%netflix%'
      or {{ description_col }} ilike '%spotify%'
      or {{ description_col }} ilike '%amazon prime%'
      or {{ description_col }} ilike '%adobe%'
      or {{ description_col }} ilike '%icloud%'
      or {{ description_col }} ilike '%nytimes%'
      or {{ description_col }} ilike '%planet fitness%'
        then 'Subscriptions'

    when {{ description_col }} ilike '%rent%'
        then 'Rent'

    when {{ description_col }} ilike '%national grid%'
      or {{ description_col }} ilike '%verizon%'
      or {{ description_col }} ilike '%xfinity%'
      or {{ description_col }} ilike '%utility%'
        then 'Utilities & Bills'

    when {{ description_col }} ilike '%uber trip%'
      or {{ description_col }} ilike '%shell%'
      or {{ description_col }} ilike '%mbta%'
      or {{ description_col }} ilike '%gas%'
        then 'Transportation'

    when {{ description_col }} ilike '%amazon.com%'
      or {{ description_col }} ilike '%target%'
      or {{ description_col }} ilike '%best buy%'
        then 'Shopping'

    when {{ description_col }} ilike '%amc%'
      or {{ description_col }} ilike '%steam%'
        then 'Entertainment'

    when {{ description_col }} ilike '%delta%'
      or {{ description_col }} ilike '%airbnb%'
      or {{ description_col }} ilike '%southwest%'
      or {{ description_col }} ilike '%airlines%'
        then 'Travel'

    when {{ description_col }} ilike '%dental%'
      or {{ description_col }} ilike '%health%'
      or {{ description_col }} ilike '%medical%'
      or {{ description_col }} ilike '%cvs%'
      or {{ description_col }} ilike '%pharmacy%'
        then 'Healthcare'

    when {{ description_col }} ilike '%payroll%'
      or {{ description_col }} ilike '%direct dep%'
        then 'Income'

    else 'Uncategorized'
end
{% endmacro %}
