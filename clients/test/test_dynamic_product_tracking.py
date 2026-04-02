from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.models import Client, Employee, Product, Renewal, Sale


class DynamicProductTrackingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dynamic_test_admin",
            password="pass",
            is_staff=True,
            is_superuser=True,
        )
        self.employee = Employee.objects.create(user=self.user, role="admin", active=True)
        self.client.force_login(self.user)

    def test_all_clients_has_dynamic_product_filters(self):
        product = Product.objects.create(
            name="Ultra Dynamic",
            code="ULTRA_DYNAMIC",
            domain=Product.DOMAIN_SALE,
            is_active=True,
            display_order=1,
        )

        response = self.client.get(reverse("clients:all_clients"), HTTP_HOST="127.0.0.1")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="product_{product.id}_status"')
        self.assertContains(response, f'name="product_{product.id}_min"')

    def test_all_clients_is_blank_when_no_active_products(self):
        Product.objects.all().update(is_active=False)

        response = self.client.get(reverse("clients:all_clients"), HTTP_HOST="127.0.0.1")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="product_')

    def test_renewal_only_product_participates_in_filtering(self):
        product = Product.objects.create(
            name="Renewal Based Product",
            code="RENEW_BASED",
            domain=Product.DOMAIN_BOTH,
            is_active=True,
            display_order=2,
        )
        tracked_client = Client.objects.create(
            name="Renewal Client",
            email="RENEW@CLIENT.COM",
            phone="9999990000",
            mapped_to=self.employee,
        )

        Renewal.objects.create(
            client=tracked_client,
            product_ref=product,
            product_type=Renewal.PRODUCT_TYPE_OTHER,
            product_name=product.name,
            renewal_date=date(2026, 4, 2),
            frequency=Renewal.FREQUENCY_MONTHLY,
            employee=self.employee,
            premium_amount=1500,
            premium_collected_on=date(2026, 4, 2),
            created_by=self.user,
        )

        status_param = f"product_{product.id}_status"
        response = self.client.get(
            reverse("clients:all_clients"),
            {status_param: "yes"},
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Renewal Client")

    def test_product_management_renewal_dropdown_controls_domain(self):
        add_response = self.client.post(
            reverse("clients:product_management"),
            {
                "action": "add",
                "name": "Dropdown Controlled",
                "code": "DROPDOWN_CTRL",
                "renewal_tracked": "yes",
                "display_order": "10",
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(add_response.status_code, 302)

        product = Product.objects.get(code="DROPDOWN_CTRL")
        self.assertEqual(product.domain, Product.DOMAIN_BOTH)

        update_response = self.client.post(
            reverse("clients:product_management"),
            {
                "action": "update",
                "product_id": product.id,
                "name": "Dropdown Controlled",
                "code": "DROPDOWN_CTRL",
                "renewal_tracked": "no",
                "display_order": "11",
            },
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(update_response.status_code, 302)

        product.refresh_from_db()
        self.assertEqual(product.domain, Product.DOMAIN_SALE)
