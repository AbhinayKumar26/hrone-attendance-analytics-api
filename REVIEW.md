# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Employee creation | Duplicate employee codes must not create duplicate employees. | Submit the same `emp_code` twice; the second request returns `409`. | Added a unique index and duplicate-key handling. |
| 2 | Punch-in | Duplicate daily attendance records must be prevented during concurrent requests. | Send two punch-in requests for the same employee and date. | Added a unique compound index on `emp_code` and `date`. |
| 3 | Punch-out | Work hours and overtime must be calculated correctly. | Test punch-out times before and after shift end. | Implemented work-hour and overtime calculations. |
| 4 | Attendance regularization | Corrections should preserve an audit trail. | Update an attendance status and inspect `history`. | Added correction history containing the actor, reason, and changed values. |
| 5 | Attendance listing | Invalid pagination or date filters should not produce incorrect results. | Test invalid page values and date ranges. | Added input validation and pagination handling. |

## Items checked

The duplicate employee test returned `409`, confirming that duplicate employee codes are rejected. Attendance listing and regularization tests returned `200`, and the regularization response contained a history entry. Analytics and the MongoDB explain endpoint also returned successful responses.

The items above describe behaviors verified in the implemented application. Compare them with the original starter code before claiming each one was a defect in the starter.
