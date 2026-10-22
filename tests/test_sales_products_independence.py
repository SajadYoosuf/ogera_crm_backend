"""Sales <-> Products independence (qa_checklist.yaml, sales_products section).

The model comment in models/sale.py already states the design intent: "Deliberately not linked
to contacts or the products catalogue." This proves it end to end through the real app (both
routers mounted, unlike test_sales.py/test_products.py's isolated single-router harnesses):
renaming a catalogue product must not change the free-text product name on a past sale.
"""


def _admin(login):
    headers, _ = login("admin")
    return headers


def test_renaming_a_catalogue_product_does_not_change_past_sales(client, login):
    admin = _admin(login)

    product = client.post("/products", headers=admin, json={
        "name": "200 LTR Vertical Tank", "slug": "200-ltr-vertical-tank-indep-test",
        "capacity_litres": 200, "orientation": "vertical", "mrp": "6000", "sale_price": "5400",
        "main_image_url": "https://example.com/tank.jpg",
    })
    assert product.status_code == 201, product.text

    sale = client.post("/sales", headers=admin, json={
        "name": "Test Buyer", "phone": "9123456780", "purchased_product": "200 LTR Vertical Tank",
        "purchase_date": "2026-01-10",
    })
    assert sale.status_code == 201, sale.text
    sale_id = sale.json()["id"]

    renamed = client.patch(f"/products/{product.json()['id']}", headers=admin,
                           json={"name": "500 LTR Vertical Tank (Renamed)"})
    assert renamed.status_code == 200, renamed.text

    unchanged = client.get(f"/sales/{sale_id}", headers=admin)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["purchased_product"] == "200 LTR Vertical Tank", \
        "a sale's product text must not follow a later catalogue rename"


def test_deleting_the_catalogue_product_does_not_touch_the_sale_either(client, login):
    admin = _admin(login)
    product = client.post("/products", headers=admin, json={
        "name": "Deletable Tank", "slug": "deletable-tank-indep-test",
        "capacity_litres": 100, "orientation": "horizontal", "mrp": "3000", "sale_price": "2700",
        "main_image_url": "https://example.com/tank2.jpg",
    }).json()

    sale = client.post("/sales", headers=admin, json={
        "name": "Another Buyer", "phone": "9123456781", "purchased_product": "Deletable Tank",
        "purchase_date": "2026-01-11",
    }).json()

    archived = client.delete(f"/products/{product['id']}", headers=admin)
    assert archived.status_code in (200, 204), archived.text

    still_there = client.get(f"/sales/{sale['id']}", headers=admin)
    assert still_there.status_code == 200
    assert still_there.json()["purchased_product"] == "Deletable Tank"
