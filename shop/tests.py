from io import BytesIO
import smtplib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .forms import ProductForm
from .models import Category, Message, MessageThread, NewsletterSubscriber, Order, Product, ProductImage, SellerProfile


class ProductGalleryTests(TestCase):
    def make_image(self, name='sample.png', color=(22, 99, 255)):
        buffer = BytesIO()
        Image.new('RGB', (120, 120), color=color).save(buffer, format='PNG')
        buffer.seek(0)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')

    def test_home_page_loads(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        Product.objects.create(
            name='Demo Speaker',
            slug='demo-speaker',
            description='Demo product description',
            price=120,
            category=category,
            image='shop/product-speaker.svg',
            stock=10,
            is_featured=True,
        )

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Demo Speaker')

    def test_product_detail_renders_gallery_images(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        product = Product.objects.create(
            name='Gallery Headphones',
            slug='gallery-headphones',
            description='A product with gallery images',
            price=140,
            category=category,
            image='shop/product-headphones.svg',
            stock=8,
            is_featured=True,
        )
        ProductImage.objects.create(product=product, image=self.make_image('a.png'), is_primary=True)
        ProductImage.objects.create(product=product, image=self.make_image('b.png'))

        response = self.client.get(reverse('product_detail', args=[product.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/media/')

    def test_form_rejects_more_than_eight_images(self):
        files = [self.make_image(f'image-{i}.png') for i in range(9)]
        form = ProductForm(
            data={
                'name': 'Test Product',
                'slug': 'test-product',
                'category': Category.objects.create(name='Tech', slug='tech', description='Tech').id,
                'price': 100,
                'stock': 5,
                'image': 'shop/product-watch.svg',
                'description': 'Test description',
                'is_featured': True,
            },
            files={'images': files},
        )

        self.assertFalse(form.is_valid())
        self.assertIn('You can upload up to 8 images per product.', form.errors['images'])


class CartFlowTests(TestCase):
    def test_add_to_cart_creates_session_entry(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        product = Product.objects.create(
            name='Portable Speaker',
            slug='portable-speaker',
            description='A portable speaker for active lifestyles.',
            price=130,
            category=category,
            image='shop/product-speaker.svg',
            stock=5,
            is_featured=True,
        )

        response = self.client.post(reverse('add_to_cart', args=[product.slug]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('cart', {}).get(str(product.id)), 1)

    def test_cart_page_loads_with_total_summary(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        product = Product.objects.create(
            name='Travel Headset',
            slug='travel-headset',
            description='Travel-ready headset.',
            price=200,
            category=category,
            image='shop/product-headphones.svg',
            stock=7,
            is_featured=True,
        )
        session = self.client.session
        session['cart'] = {str(product.id): 2}
        session.save()

        response = self.client.get(reverse('cart'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Travel Headset')
        self.assertContains(response, '$400.00')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_signup_email_sends_code_and_redirects_to_verification(self):
        response = self.client.post(reverse('signup_email'), {'email': 'new-user@example.com'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('signup_email'), 'new-user@example.com')
        self.assertIn('signup_code', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend')
    @patch('shop.views.send_mail', side_effect=smtplib.SMTPAuthenticationError(535, b'Authentication failed'))
    def test_signup_email_shows_an_error_when_smtp_fails(self, send_mail_mock):
        response = self.client.post(
            reverse('signup_email'),
            {'email': 'new-user@example.com'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'could not send the verification email')
        self.assertNotIn('signup_code', self.client.session)
        send_mail_mock.assert_called_once()

    def test_signup_complete_creates_user_after_verification(self):
        User = get_user_model()
        session = self.client.session
        session['signup_email'] = 'new-user@example.com'
        session['signup_code'] = '123456'
        session['signup_verified'] = True
        session.save()

        response = self.client.post(reverse('signup_complete'), {
            'username': 'newuser',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email='new-user@example.com', username='newuser').exists())

    def test_wishlist_toggle_saves_product_for_guest(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        product = Product.objects.create(
            name='Night Lamp',
            slug='night-lamp',
            description='Modern lamp for sleeping spaces.',
            price=75,
            category=category,
            image='shop/product-watch.svg',
            stock=4,
            is_featured=True,
        )

        response = self.client.post(reverse('wishlist_toggle', args=[product.slug]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(product.id, self.client.session.get('wishlist', []))

    def test_wishlist_toggle_rejects_get_requests(self):
        product = Product.objects.create(
            name='Desk Clock',
            slug='desk-clock',
            description='A clean desk clock.',
            price=40,
            stock=3,
        )

        response = self.client.get(reverse('wishlist_toggle', args=[product.slug]))

        self.assertEqual(response.status_code, 405)

    def test_wishlist_toggle_rejects_external_referer_redirects(self):
        product = Product.objects.create(
            name='Ceramic Mug',
            slug='ceramic-mug',
            description='Hand-finished ceramic mug.',
            price=18,
            stock=2,
        )

        response = self.client.post(
            reverse('wishlist_toggle', args=[product.slug]),
            HTTP_REFERER='https://malicious.example/',
        )

        self.assertEqual(response.url, reverse('product_list'))

    def test_checkout_page_creates_order_from_cart(self):
        category = Category.objects.create(name='Audio', slug='audio', description='Test music gear')
        product = Product.objects.create(
            name='Desk Lamp',
            slug='desk-lamp',
            description='A desk lamp for modern offices.',
            price=65,
            category=category,
            image='shop/product-watch.svg',
            stock=9,
            is_featured=True,
        )
        session = self.client.session
        session['cart'] = {str(product.id): 2}
        session.save()

        response = self.client.post(reverse('checkout'), {
            'customer_name': 'Ava Smith',
            'customer_email': 'ava@example.com',
            'customer_phone': '+123456789',
            'address': '12 Market Street, Tehran',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Order.objects.filter(customer_name='Ava Smith').exists())
        self.assertEqual(self.client.session.get('cart', {}), {})
        product.refresh_from_db()
        self.assertEqual(product.stock, 7)
        success_page = self.client.get(response.url)
        self.assertEqual(success_page.status_code, 200)
        self.assertContains(success_page, 'Thanks for your order!')

    def test_checkout_rejects_invalid_email(self):
        product = Product.objects.create(
            name='Travel Mug',
            slug='travel-mug',
            description='A sturdy travel mug.',
            price=25,
            stock=2,
        )
        session = self.client.session
        session['cart'] = {str(product.id): 1}
        session.save()

        response = self.client.post(reverse('checkout'), {
            'customer_name': 'Ava Smith',
            'customer_email': 'not-an-email',
            'customer_phone': '',
            'address': '12 Market Street',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_newsletter_subscription_is_saved_and_validated(self):
        response = self.client.post(reverse('newsletter_subscribe'), {'email': 'reader@example.com'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(NewsletterSubscriber.objects.filter(email='reader@example.com').exists())

        self.client.post(reverse('newsletter_subscribe'), {'email': 'invalid'})
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)


class AuthorizationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(username='seller', password='StrongPass123!')
        self.buyer = User.objects.create_user(username='buyer', password='StrongPass123!')
        self.other_user = User.objects.create_user(username='other', password='StrongPass123!')
        self.product = Product.objects.create(
            name='Studio Headphones',
            slug='studio-headphones',
            description='Closed-back studio headphones.',
            price=150,
            stock=3,
            seller=self.seller,
        )
        self.thread = MessageThread.objects.create(
            product=self.product,
            seller=self.seller,
            buyer=self.buyer,
        )

    def test_messages_requires_authentication(self):
        response = self.client.get(reverse('messages'), {'thread': self.thread.id})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_user_cannot_read_another_users_conversation(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse('messages'), {'thread': self.thread.id})

        self.assertEqual(response.status_code, 404)

    def test_user_cannot_post_to_another_users_conversation(self):
        self.client.force_login(self.other_user)

        response = self.client.post(reverse('messages'), {
            'thread_id': self.thread.id,
            'message': 'Private message',
        })

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Message.objects.filter(content='Private message').exists())

    def test_contact_page_does_not_create_a_conversation_until_message_is_sent(self):
        new_product = Product.objects.create(
            name='Studio Microphone',
            slug='studio-microphone',
            description='A broadcast microphone.',
            price=90,
            stock=2,
            seller=self.seller,
        )
        self.client.force_login(self.buyer)
        params = {'product': new_product.slug, 'seller': self.seller.id}

        response = self.client.get(reverse('messages'), params)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MessageThread.objects.count(), 1)
        response = self.client.post(reverse('messages'), {
            **params,
            'message': 'Is this still available?',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(MessageThread.objects.count(), 2)
        self.assertTrue(Message.objects.filter(content='Is this still available?').exists())

    def test_profile_edit_requires_authentication(self):
        response = self.client.get(reverse('update_profile'))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SellerProfile.objects.exists())

    def test_seller_can_only_edit_their_own_profile(self):
        seller_profile = SellerProfile.objects.create(user=self.seller, business_name='Seller Shop')
        self.client.force_login(self.other_user)

        response = self.client.post(reverse('update_profile'), {
            'business_name': 'Changed Shop',
            'phone': '',
            'city': 'Tehran',
            'bio': 'Updated profile',
            'avatar': 'shop/default-avatar.svg',
        })

        self.assertEqual(response.status_code, 302)
        seller_profile.refresh_from_db()
        self.assertEqual(seller_profile.business_name, 'Seller Shop')
        self.assertEqual(
            SellerProfile.objects.get(user=self.other_user).business_name,
            'Changed Shop',
        )

    def test_profile_form_does_not_allow_self_assigned_rating(self):
        from .forms import SellerProfileForm

        form = SellerProfileForm()

        self.assertNotIn('rating', form.fields)

    def test_seller_dashboard_requires_authentication(self):
        response = self.client.get(reverse('seller_dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_signup_verification_limits_attempts(self):
        session = self.client.session
        session['signup_email'] = 'new-user@example.com'
        session['signup_code'] = '123456'
        session['signup_code_created_at'] = timezone.now().timestamp()
        session['signup_code_attempts'] = 5
        session.save()

        response = self.client.post(reverse('signup_verify'), {'code': '654321'})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('signup_code', self.client.session)

    def test_signup_rejects_passwords_that_fail_django_validators(self):
        session = self.client.session
        session['signup_email'] = 'new-user@example.com'
        session['signup_verified'] = True
        session.save()

        response = self.client.post(reverse('signup_complete'), {
            'username': 'newuser',
            'password': 'password',
            'password_confirm': 'password',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'password')
        self.assertFalse(get_user_model().objects.filter(username='newuser').exists())

    def test_order_confirmation_is_private_to_the_order_session(self):
        order = Order.objects.create(
            customer_name='Buyer',
            address='Private address',
            total=25,
        )

        response = self.client.get(reverse('checkout_success', args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

    def test_account_dashboard_requires_authentication(self):
        response = self.client.get(reverse('account'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_account_dashboard_only_shows_the_signed_in_users_orders(self):
        own_order = Order.objects.create(
            user=self.buyer,
            customer_name='Buyer',
            address='My address',
            total=120,
        )
        Order.objects.create(
            user=self.other_user,
            customer_name='Other',
            address='Private address',
            total=90,
        )
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('account'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Order #{own_order.id}')
        self.assertNotContains(response, 'Private address')

    def test_account_profile_updates_only_personal_name_fields(self):
        self.client.force_login(self.buyer)

        response = self.client.post(reverse('update_account'), {
            'first_name': 'Alex',
            'last_name': 'Morgan',
            'email': 'attacker@example.com',
        })

        self.assertRedirects(response, reverse('account'))
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.first_name, 'Alex')
        self.assertEqual(self.buyer.last_name, 'Morgan')
        self.assertEqual(self.buyer.email, '')


class PersonalizedShoppingTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name='Audio',
            slug='audio',
            description='Audio equipment',
        )
        self.viewed_product = Product.objects.create(
            name='Studio Headphones',
            slug='studio-headphones',
            description='Closed-back studio headphones.',
            price=150,
            category=self.category,
            stock=5,
        )
        self.recommended_product = Product.objects.create(
            name='Portable Speaker',
            slug='portable-speaker',
            description='A portable wireless speaker.',
            price=80,
            category=self.category,
            stock=4,
        )
        self.other_category = Category.objects.create(
            name='Home',
            slug='home',
            description='Home essentials',
        )
        self.unrelated_product = Product.objects.create(
            name='Ceramic Vase',
            slug='ceramic-vase',
            description='A handmade vase.',
            price=45,
            category=self.other_category,
            stock=2,
        )

    def test_product_view_is_remembered_and_recommendations_match_category(self):
        response = self.client.get(reverse('product_detail', args=[self.viewed_product.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session['recently_viewed'], [self.viewed_product.id])

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        recommendations = response.context['recommended_products']
        self.assertIn(self.recommended_product, recommendations)
        self.assertNotIn(self.viewed_product, recommendations)
        self.assertNotIn(self.unrelated_product, recommendations)
        self.assertContains(response, 'A little more you')

    def test_recently_viewed_list_is_unique_and_capped(self):
        self.client.get(reverse('product_detail', args=[self.viewed_product.slug]))
        second_product = Product.objects.create(
            name='Wireless Earbuds',
            slug='wireless-earbuds',
            description='Compact wireless earbuds.',
            price=60,
            category=self.category,
            stock=8,
        )
        self.client.get(reverse('product_detail', args=[second_product.slug]))
        self.client.get(reverse('product_detail', args=[self.viewed_product.slug]))

        self.assertEqual(
            self.client.session['recently_viewed'],
            [self.viewed_product.id, second_product.id],
        )

    def test_comparison_add_remove_and_limit(self):
        self.client.post(reverse('compare_toggle', args=[self.viewed_product.slug]))
        self.client.post(reverse('compare_toggle', args=[self.recommended_product.slug]))

        response = self.client.get(reverse('product_compare'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Find your')
        self.assertEqual(
            [product.id for product in response.context['products']],
            [self.viewed_product.id, self.recommended_product.id],
        )

        self.client.post(reverse('compare_toggle', args=[self.viewed_product.slug]))
        self.assertEqual(
            self.client.session['compare_products'],
            [self.recommended_product.id],
        )

    def test_comparison_is_limited_to_four_products(self):
        product_ids = []
        for index in range(5):
            product = Product.objects.create(
                name=f'Audio product {index}',
                slug=f'audio-product-{index}',
                description='Another audio product.',
                price=20 + index,
                category=self.category,
                stock=1,
            )
            product_ids.append(product.id)
            self.client.post(reverse('compare_toggle', args=[product.slug]))

        self.assertEqual(self.client.session['compare_products'], product_ids[:4])
        self.assertEqual(
            len(self.client.get(reverse('product_compare')).context['products']),
            4,
        )

    def test_comparison_rejects_get_requests(self):
        response = self.client.get(
            reverse('compare_toggle', args=[self.viewed_product.slug])
        )

        self.assertEqual(response.status_code, 405)
