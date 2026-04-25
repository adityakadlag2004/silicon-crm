"""One-shot: copy working data out of crmdb (OfficeDev schema) into silicondb (this project).

Schema differences handled:
- clients_client: source has `attributes jsonb`; target has individual portfolio columns.
  All source rows observed have empty `{}` so portfolio columns are left at defaults.
- clients_sale: source `attributes.policy_type` → target `policy_type` column.
- clients_renewal: source has no product_type/product_name; target needs them —
  derive from `clients_product` via `product_ref_id`.
- Extra source columns (organization_id, proof_image, review_notes, lifecycle,
  renewal_period_months, eligibility_predicate) are dropped; they don't exist in this schema.
"""
import psycopg2
import psycopg2.extras

SOURCE = dict(dbname="crmdb", user="crmuser", password="NewStrongPassword123!", host="localhost", port=5432)
TARGET = dict(dbname="silicondb", user="crmuser", password="NewStrongPassword123!", host="localhost", port=5432)


def copy_rows(src_cur, dst_cur, select_sql, insert_sql, transform=None, label=""):
    src_cur.execute(select_sql)
    rows = src_cur.fetchall()
    n = 0
    for row in rows:
        values = transform(row) if transform else dict(row)
        dst_cur.execute(insert_sql, values)
        n += 1
    print(f"  {label}: {n} rows")
    return n


def main():
    src = psycopg2.connect(**SOURCE)
    dst = psycopg2.connect(**TARGET)
    src.autocommit = False
    dst.autocommit = False
    src_cur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dst_cur = dst.cursor()

    # Wipe target tables (children first). Keep django_migrations / content types etc.
    print("Truncating target tables…")
    dst_cur.execute("""
        TRUNCATE TABLE
            clients_incentiveslab,
            clients_incentiverule,
            clients_renewal,
            clients_sale,
            clients_monthlytargethistory,
            clients_client,
            clients_product,
            clients_firmsettings,
            clients_employee,
            auth_user
        RESTART IDENTITY CASCADE;
    """)

    # 1. auth_user
    print("Copying auth_user…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, password, last_login, is_superuser, username, first_name, last_name, email, is_staff, is_active, date_joined FROM auth_user ORDER BY id",
        "INSERT INTO auth_user (id, password, last_login, is_superuser, username, first_name, last_name, email, is_staff, is_active, date_joined) VALUES (%(id)s, %(password)s, %(last_login)s, %(is_superuser)s, %(username)s, %(first_name)s, %(last_name)s, %(email)s, %(is_staff)s, %(is_active)s, %(date_joined)s)",
        label="auth_user",
    )

    # 2. clients_employee (drop organization_id)
    print("Copying clients_employee…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, role, user_id, salary, active, employee_number FROM clients_employee ORDER BY id",
        "INSERT INTO clients_employee (id, role, user_id, salary, active, employee_number) VALUES (%(id)s, %(role)s, %(user_id)s, %(salary)s, %(active)s, %(employee_number)s)",
        label="clients_employee",
    )

    # 3. clients_product (drop organization_id, lifecycle, renewal_period_months)
    print("Copying clients_product…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, name, code, domain, display_order, is_active, archived_at, archived_reason, created_at, updated_at FROM clients_product ORDER BY id",
        "INSERT INTO clients_product (id, name, code, domain, display_order, is_active, archived_at, archived_reason, created_at, updated_at) VALUES (%(id)s, %(name)s, %(code)s, %(domain)s, %(display_order)s, %(is_active)s, %(archived_at)s, %(archived_reason)s, %(created_at)s, %(updated_at)s)",
        label="clients_product",
    )

    # 4. clients_firmsettings
    print("Copying clients_firmsettings…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, firm_name, address, email, phone, website, logo, primary_color, updated_at FROM clients_firmsettings ORDER BY id",
        "INSERT INTO clients_firmsettings (id, firm_name, address, email, phone, website, logo, primary_color, updated_at) VALUES (%(id)s, %(firm_name)s, %(address)s, %(email)s, %(phone)s, %(website)s, %(logo)s, %(primary_color)s, %(updated_at)s)",
        label="clients_firmsettings",
    )

    # 5. clients_client — ignore attributes (observed empty); fill portfolio columns with defaults.
    print("Copying clients_client…")
    src_cur.execute(
        "SELECT id, name, email, phone, pan, address, status, created_at, mapped_to_id, date_of_birth, edited_at, edited_by_id, attributes FROM clients_client ORDER BY id"
    )
    cnt = 0
    for row in src_cur.fetchall():
        attrs = row["attributes"] or {}
        dst_cur.execute(
            """
            INSERT INTO clients_client
            (id, name, email, phone, pan, address, status, created_at, mapped_to_id,
             date_of_birth, edited_at, edited_by_id,
             sip_status, sip_amount, sip_topup,
             health_status, health_cover, health_topup, health_product,
             life_status, life_cover, life_product,
             motor_status, motor_insured_value, motor_product,
             pms_status, pms_amount, pms_start_date,
             lumsum_investment)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s)
            """,
            (
                row["id"], row["name"], row["email"], row["phone"], row["pan"], row["address"],
                row["status"], row["created_at"], row["mapped_to_id"], row["date_of_birth"],
                row["edited_at"], row["edited_by_id"],
                bool(attrs.get("sip_status", False)), attrs.get("sip_amount"), attrs.get("sip_topup"),
                bool(attrs.get("health_status", False)), attrs.get("health_cover"), attrs.get("health_topup"), attrs.get("health_product"),
                bool(attrs.get("life_status", False)), attrs.get("life_cover"), attrs.get("life_product"),
                bool(attrs.get("motor_status", False)), attrs.get("motor_insured_value"), attrs.get("motor_product"),
                bool(attrs.get("pms_status", False)), attrs.get("pms_amount"), attrs.get("pms_start_date"),
                attrs.get("lumsum_investment", 0),
            ),
        )
        cnt += 1
    print(f"  clients_client: {cnt} rows")

    # 6. clients_sale — extract policy_type from attributes; drop proof_image/review_notes/organization_id/attributes
    print("Copying clients_sale…")
    src_cur.execute(
        "SELECT id, product, amount, date, client_id, employee_id, created_at, incentive_amount, points, updated_at, cover_amount, approved_at, approved_by_id, status, rejection_reason, product_name_snapshot, product_ref_id, attributes FROM clients_sale ORDER BY id"
    )
    cnt = 0
    for row in src_cur.fetchall():
        attrs = row["attributes"] or {}
        policy_type = attrs.get("policy_type", "") or ""
        dst_cur.execute(
            """
            INSERT INTO clients_sale
            (id, product, amount, date, client_id, employee_id, created_at, incentive_amount,
             points, updated_at, cover_amount, approved_at, approved_by_id, status,
             rejection_reason, product_name_snapshot, product_ref_id, policy_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["id"], row["product"], row["amount"], row["date"], row["client_id"],
                row["employee_id"], row["created_at"], row["incentive_amount"], row["points"],
                row["updated_at"], row["cover_amount"], row["approved_at"], row["approved_by_id"],
                row["status"], row["rejection_reason"], row["product_name_snapshot"],
                row["product_ref_id"], policy_type,
            ),
        )
        cnt += 1
    print(f"  clients_sale: {cnt} rows")

    # 7. clients_renewal — need product_type/product_name from linked product
    print("Copying clients_renewal…")
    src_cur.execute(
        """
        SELECT r.id, r.renewal_date, r.frequency, r.notes, r.created_at, r.client_id,
               r.created_by_id, r.employee_id, r.premium_amount, r.premium_collected_on,
               r.renewal_end_date, r.product_ref_id, p.name AS product_name, p.domain AS product_domain
        FROM clients_renewal r
        LEFT JOIN clients_product p ON p.id = r.product_ref_id
        ORDER BY r.id
        """
    )
    cnt = 0
    for row in src_cur.fetchall():
        # Target product_type is one of: life_insurance, health_insurance, other.
        pname = (row["product_name"] or "").lower()
        if "life" in pname:
            product_type = "life_insurance"
        elif "health" in pname:
            product_type = "health_insurance"
        else:
            product_type = "other"
        dst_cur.execute(
            """
            INSERT INTO clients_renewal
            (id, product_type, product_name, renewal_date, frequency, notes, created_at,
             client_id, created_by_id, employee_id, premium_amount, premium_collected_on,
             renewal_end_date, product_ref_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["id"], product_type, row["product_name"], row["renewal_date"],
                row["frequency"], row["notes"], row["created_at"], row["client_id"],
                row["created_by_id"], row["employee_id"], row["premium_amount"],
                row["premium_collected_on"], row["renewal_end_date"], row["product_ref_id"],
            ),
        )
        cnt += 1
    print(f"  clients_renewal: {cnt} rows")

    # 8. clients_incentiverule (drop organization_id, eligibility_predicate)
    print("Copying clients_incentiverule…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, product, unit_amount, points_per_unit, active, product_ref_id FROM clients_incentiverule ORDER BY id",
        "INSERT INTO clients_incentiverule (id, product, unit_amount, points_per_unit, active, product_ref_id) VALUES (%(id)s, %(product)s, %(unit_amount)s, %(points_per_unit)s, %(active)s, %(product_ref_id)s)",
        label="clients_incentiverule",
    )

    # 9. clients_incentiveslab
    print("Copying clients_incentiveslab…")
    copy_rows(
        src_cur, dst_cur,
        "SELECT id, threshold, payout, label, rule_id FROM clients_incentiveslab ORDER BY id",
        "INSERT INTO clients_incentiveslab (id, threshold, payout, label, rule_id) VALUES (%(id)s, %(threshold)s, %(payout)s, %(label)s, %(rule_id)s)",
        label="clients_incentiveslab",
    )

    # Fix sequences so new inserts don't collide with copied ids.
    print("Resetting sequences…")
    for table, col in [
        ("auth_user", "id"),
        ("clients_employee", "id"),
        ("clients_product", "id"),
        ("clients_firmsettings", "id"),
        ("clients_sale", "id"),
        ("clients_renewal", "id"),
        ("clients_incentiverule", "id"),
        ("clients_incentiveslab", "id"),
    ]:
        dst_cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), COALESCE((SELECT MAX({col}) FROM {table}), 1))"
        )

    dst.commit()
    print("Done.")
    src.close()
    dst.close()


if __name__ == "__main__":
    main()
