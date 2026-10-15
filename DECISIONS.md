# DECISIONS.md

1. **Indexes:** I created a unique index on `emp_code` to prevent duplicate employees, a unique compound index on `emp_code` and `date` to prevent duplicate daily attendance records, and a compound index on `date` and `emp_code` to support attendance listing and sorting. I would consider additional indexes only after checking query execution statistics.

2. **Punch-in race:** If two identical punch-in requests arrive simultaneously, the unique compound index prevents duplicate attendance records for the same employee and date. The endpoint's duplicate-key error handling determines the response returned to the losing request.

3. **Ties:** The leaderboard's behavior when employees have equal late minutes at the `limit` boundary depends on its ranking, sorting, and limiting stages. These should follow the API contract.

4. **Headcount:** The department summary calculates headcount from employee records instead of attendance logs alone, allowing employees without attendance records to be counted.

5. **Scaling:** For 100 times more data, I would analyze query execution plans, optimize aggregation pipelines, and add indexes only when measurements justify their cost.
